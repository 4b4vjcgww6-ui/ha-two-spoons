"""The Two Spoons integration: store to-do lists, a meals calendar and week sensors."""

from __future__ import annotations

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
import voluptuous as vol

from .api import (
    TwoSpoonsApiError,
    TwoSpoonsAuthError,
    TwoSpoonsClient,
    TwoSpoonsError,
    TwoSpoonsFirewallError,
    TwoSpoonsNoShoppingWeekError,
    TwoSpoonsTimeoutError,
)
from .const import CONF_API_TOKEN, DOMAIN, ERR_UNKNOWN_STORE, MAX_ITEM_TEXT
from .coordinator import TwoSpoonsConfigEntry, TwoSpoonsCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR, Platform.TODO]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_REFRESH = "refresh"
SERVICE_ADD_ITEM = "add_item"

ATTR_ITEM = "item"
ATTR_STORE = "store"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

ADD_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ITEM): cv.string,
        vol.Optional(ATTR_STORE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register two_spoons.refresh and two_spoons.add_item."""

    async def _refresh(call: ServiceCall) -> None:
        entries: list[TwoSpoonsConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        for entry in entries:
            coordinator = entry.runtime_data
            await coordinator.async_refresh()
            if not coordinator.last_update_success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="refresh_failed"
                ) from coordinator.last_exception

    async def _add_item(call: ServiceCall) -> ServiceResponse:
        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        coordinator = entry.runtime_data
        text = call.data[ATTR_ITEM].strip()
        if not text or len(text) > MAX_ITEM_TEXT:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_item")
        store = None
        if raw_store := call.data.get(ATTR_STORE, "").strip():
            store = raw_store.upper()
            lists = coordinator.data.lists if coordinator.data else None
            if lists is not None and store not in lists.stores:
                raise _unknown_store(store, list(lists.stores))

        try:
            added = await coordinator.client.async_add_item(text, store)
        except TwoSpoonsError as err:
            raise _add_item_error(hass, entry, err, store) from err
        finally:
            await coordinator.async_refresh()

        if not call.return_response:
            return None
        return {
            "summary": added.summary,
            "store_code": added.store_code,
            "store_name": added.store_name,
            "merged": added.merged,
            "merged_into_summary": added.summary if added.merged else None,
        }

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_ITEM,
        _add_item,
        schema=ADD_ITEM_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


def _target_entry(hass: HomeAssistant, entry_id: str | None) -> TwoSpoonsConfigEntry:
    """The entry an action is for: the one asked for, or the only one."""
    entries: list[TwoSpoonsConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry_id is not None:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="choose_entry")
    return entries[0]


def _unknown_store(store: str, codes: list[str]) -> ServiceValidationError:
    if not codes:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_store_code",
            translation_placeholders={"store": store},
        )
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_store",
        translation_placeholders={"store": store, "codes": ", ".join(codes)},
    )


def _add_item_error(
    hass: HomeAssistant, entry: TwoSpoonsConfigEntry, err: TwoSpoonsError, store: str | None
) -> HomeAssistantError:
    """A failed add, worded to be read aloud."""
    if isinstance(err, TwoSpoonsApiError):
        if err.code == ERR_UNKNOWN_STORE and store is not None:
            return _unknown_store(store, [])
        if err.status == 400:
            return ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_item")
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="add_item_rejected",
            translation_placeholders={"code": err.code},
        )
    if isinstance(err, TwoSpoonsAuthError):
        entry.async_start_reauth(hass)
        key = "add_item_invalid_auth"
    elif isinstance(err, TwoSpoonsFirewallError):
        key = "add_item_blocked_by_firewall"
    elif isinstance(err, TwoSpoonsNoShoppingWeekError):
        key = "add_item_no_shopping_week"
    elif isinstance(err, TwoSpoonsTimeoutError):
        key = "add_item_timeout"
    else:
        key = "add_item_cannot_connect"
    return HomeAssistantError(translation_domain=DOMAIN, translation_key=key)


async def async_setup_entry(hass: HomeAssistant, entry: TwoSpoonsConfigEntry) -> bool:
    """Connect, fetch the lists once and set up the to-do platform."""
    client = await async_create_client(hass, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    coordinator = TwoSpoonsCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TwoSpoonsConfigEntry) -> bool:
    """Unload the to-do platform."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_create_client(hass: HomeAssistant, base_url: str, token: str) -> TwoSpoonsClient:
    """A client on HA's shared session, identifying itself with our version."""
    integration = await async_get_integration(hass, DOMAIN)
    return TwoSpoonsClient(
        async_get_clientsession(hass),
        base_url,
        token,
        f"HomeAssistant-TwoSpoons/{integration.version}",
    )
