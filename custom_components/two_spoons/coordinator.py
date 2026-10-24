"""Polls GET /lists, /meals and /status for the Two Spoons integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Meals,
    ShoppingLists,
    Status,
    TwoSpoonsAuthError,
    TwoSpoonsClient,
    TwoSpoonsError,
    TwoSpoonsFirewallError,
    TwoSpoonsNoShoppingWeekError,
)
from .const import CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)

type TwoSpoonsConfigEntry = ConfigEntry[TwoSpoonsCoordinator]

# After a firewall block, wait this long before trying again.
FIREWALL_BACKOFF = timedelta(minutes=15)
ISSUE_FIREWALL = "blocked_by_firewall"


@dataclass(frozen=True, slots=True)
class TwoSpoonsData:
    """One poll's results. A part is None when the API reported no shopping
    week for it (409); those entities show as unavailable."""

    lists: ShoppingLists | None
    meals: Meals | None
    status: Status | None


class TwoSpoonsCoordinator(DataUpdateCoordinator[TwoSpoonsData]):
    """Fetches /lists, /meals and /status, one after another, each cycle."""

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
            # Data compares by value, so an unchanged poll doesn't rewrite state.
            always_update=False,
        )
        self.client = client

    async def _fetch[T](self, call: Callable[[], Awaitable[T]]) -> T | None:
        try:
            return await call()
        except TwoSpoonsNoShoppingWeekError:
            return None

    async def _async_update_data(self) -> TwoSpoonsData:
        try:
            data = TwoSpoonsData(
                lists=await self._fetch(self.client.async_get_lists),
                meals=await self._fetch(self.client.async_get_meals),
                status=await self._fetch(self.client.async_get_week_status),
            )
        except TwoSpoonsAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except TwoSpoonsFirewallError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_FIREWALL,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_FIREWALL,
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="blocked_by_firewall",
                retry_after=FIREWALL_BACKOFF.total_seconds(),
            ) from err
        except TwoSpoonsError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect") from err
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_FIREWALL)
        return data
