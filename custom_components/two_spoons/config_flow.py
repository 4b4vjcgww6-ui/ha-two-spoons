"""Config, reauth, reconfigure and options flows for Two Spoons."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol
from yarl import URL

from . import async_create_client
from .api import (
    TwoSpoonsAuthError,
    TwoSpoonsError,
    TwoSpoonsFirewallError,
    TwoSpoonsNoShoppingWeekError,
    normalize_base_url,
)
from .const import (
    CONF_API_TOKEN,
    CONF_POLL_MINUTES,
    DEFAULT_POLL_MINUTES,
    DOMAIN,
    MAX_POLL_MINUTES,
    MIN_POLL_MINUTES,
)
from .coordinator import TwoSpoonsConfigEntry

_LOGGER = logging.getLogger(__name__)

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _title(base_url: str) -> str:
    return URL(base_url).host or base_url


class TwoSpoonsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Two Spoons with a site URL and an API token."""

    VERSION = 1

    async def _async_check(self, base_url: str, token: str) -> str | None:
        """GET /status with these details. Returns an error key, or None if OK."""
        client = await async_create_client(self.hass, base_url, token)
        try:
            await client.async_get_status()
        except TwoSpoonsNoShoppingWeekError:
            return None  # the token was accepted; there's just no week yet
        except TwoSpoonsAuthError:
            return "invalid_auth"
        except TwoSpoonsFirewallError:
            return "blocked_by_firewall"
        except TwoSpoonsError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error checking Two Spoons")
            return "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the URL and token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            try:
                base_url = normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                if (error := await self._async_check(base_url, token)) is None:
                    return self.async_create_entry(
                        title=_title(base_url),
                        data={CONF_URL: base_url, CONF_API_TOKEN: token},
                    )
                errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_URL): URL_SELECTOR,
                vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR,
            }
        )
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, {CONF_URL: user_input[CONF_URL]})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token was rejected; ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token for the same site."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            if (error := await self._async_check(entry.data[CONF_URL], token)) is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: token}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"host": _title(entry.data[CONF_URL])},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the URL and/or token. A blank token keeps the current one."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = (user_input.get(CONF_API_TOKEN) or "").strip() or entry.data[CONF_API_TOKEN]
            try:
                base_url = normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                if base_url != entry.unique_id:
                    await self.async_set_unique_id(base_url)
                    self._abort_if_unique_id_configured()
                if (error := await self._async_check(base_url, token)) is None:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=base_url,
                        title=_title(base_url),
                        data_updates={CONF_URL: base_url, CONF_API_TOKEN: token},
                    )
                errors["base"] = error

        schema = self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Required(CONF_URL): URL_SELECTOR,
                    vol.Optional(CONF_API_TOKEN): TOKEN_SELECTOR,
                }
            ),
            {CONF_URL: (user_input or entry.data)[CONF_URL]},
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: TwoSpoonsConfigEntry) -> TwoSpoonsOptionsFlow:
        """Polling interval."""
        return TwoSpoonsOptionsFlow()


class TwoSpoonsOptionsFlow(OptionsFlowWithReload):
    """Set the polling interval; the entry reloads when it changes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_POLL_MINUTES: int(user_input[CONF_POLL_MINUTES])}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_MINUTES,
                        default=self.config_entry.options.get(
                            CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_POLL_MINUTES,
                            max=MAX_POLL_MINUTES,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    )
                }
            ),
        )
