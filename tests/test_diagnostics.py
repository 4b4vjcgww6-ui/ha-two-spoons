"""Diagnostics: token redacted, no item text."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.two_spoons.diagnostics import async_get_config_entry_diagnostics

from .conftest import BASE, TOKEN


async def test_diagnostics(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Counts per store and the entry settings, with the token redacted."""
    diag = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert diag["entry"]["data"] == {"url": BASE, "api_token": "**REDACTED**"}
    assert diag["last_update_success"] is True
    assert diag["stores"] == [
        {"code": "GG", "items": 2, "completed": 1},
        {"code": "WW", "items": 4, "completed": 3},
        {"code": "BK", "items": 1, "completed": 0},
    ]
    dumped = json.dumps(diag, ensure_ascii=False)
    assert TOKEN not in dumped
    assert "Apples" not in dumped
    assert "Ready Meals" not in dumped
    assert diag["meals"] == {"nights": 14, "planned": 7, "baking": 1, "version": "meals-v1-example"}
    assert diag["status"] == {"nights_planned": 6, "woolies_status": "exported"}
    assert "Roast chicken" not in dumped
    assert "Lemon cake" not in dumped
