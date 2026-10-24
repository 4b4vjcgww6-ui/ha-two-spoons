"""The Two Spoons integration: store to-do lists, a meals calendar and week sensors."""

from __future__ import annotations

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import TwoSpoonsClient
from .const import CONF_API_TOKEN, DOMAIN
from .coordinator import TwoSpoonsConfigEntry, TwoSpoonsCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR, Platform.TODO]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_REFRESH = "refresh"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register two_spoons.refresh."""

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

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    return True


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
