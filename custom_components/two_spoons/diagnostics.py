"""Diagnostics for Two Spoons. The token is redacted; no item or meal text is included."""

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
    """Entry settings, last poll result, and counts only."""
    coordinator = entry.runtime_data
    data = coordinator.data
    lists, meals, status = data.lists, data.meals, data.status
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
        if lists is None
        else {"week_start": lists.week_start, "version": lists.version},
        "stores": []
        if lists is None
        else [
            {
                "code": store.code,
                "items": len(store.items),
                "completed": sum(item.completed for item in store.items),
            }
            for store in lists.stores.values()
        ],
        "meals": None
        if meals is None
        else {
            "nights": len(meals.nights),
            "planned": sum(night.planned for night in meals.nights),
            "baking": len(meals.baking),
            "version": meals.version,
        },
        "status": None
        if status is None
        else {
            "nights_planned": status.week.nights_planned,
            "woolies_status": status.woolies.status,
        },
    }
