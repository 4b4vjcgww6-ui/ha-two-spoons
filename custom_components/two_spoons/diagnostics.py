"""Diagnostics for Two Spoons. The token is redacted; no item text is included."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_TOKEN
from .coordinator import TwoSpoonsConfigEntry

TO_REDACT = {CONF_API_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TwoSpoonsConfigEntry
) -> dict[str, Any]:
    """Entry settings, last poll result and per-store item counts."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "last_exception": type(coordinator.last_exception).__name__
        if coordinator.last_exception
        else None,
        "shopping_week": None
        if data is None
        else {"week_start": data.week_start, "version": data.version},
        "stores": []
        if data is None
        else [
            {
                "code": store.code,
                "items": len(store.items),
                "completed": sum(item.completed for item in store.items),
            }
            for store in data.stores.values()
        ],
    }
