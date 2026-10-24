"""Polls GET /lists for the Two Spoons integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ShoppingLists,
    TwoSpoonsAuthError,
    TwoSpoonsClient,
    TwoSpoonsError,
    TwoSpoonsFirewallError,
    TwoSpoonsNoShoppingWeekError,
)
from .const import CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)

type TwoSpoonsConfigEntry = ConfigEntry[TwoSpoonsCoordinator]


class TwoSpoonsCoordinator(DataUpdateCoordinator[ShoppingLists | None]):
    """Holds the latest lists. None means there is no shopping week right now."""

    config_entry: TwoSpoonsConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: TwoSpoonsConfigEntry, client: TwoSpoonsClient
    ) -> None:
        """Poll at the interval set in the options."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES)
            ),
            # Lists compare by value, so an unchanged poll doesn't rewrite state.
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> ShoppingLists | None:
        try:
            return await self.client.async_get_lists()
        except TwoSpoonsNoShoppingWeekError:
            # Not an error: the lists stay registered but unavailable.
            return None
        except TwoSpoonsAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except TwoSpoonsFirewallError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="blocked_by_firewall"
            ) from err
        except TwoSpoonsError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect") from err
