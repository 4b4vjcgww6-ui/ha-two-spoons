"""Week status and Woolies order sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.const import ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import DOMAIN

from .conftest import mock_lists


def sensor_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    """A sensor's entity id by its key."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None
    return entity_id


async def setup_with(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    status: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Set up with a given /status body (or status kwargs)."""
    mock_lists(aioclient_mock, lists_json, status_body=status, **kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# ── Week status ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("planned", "expected"),
    [(0, "not_started"), (1, "partial"), (6, "partial"), (7, "planned")],
)
async def test_week_status_boundaries(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    status_json: dict[str, Any],
    planned: int,
    expected: str,
) -> None:
    """0 → not_started, 1–6 → partial, 7 → planned."""
    status_json["weekStatus"]["nightsPlanned"] = planned
    await setup_with(hass, mock_entry, aioclient_mock, lists_json, status_json)
    assert hass.states.get(sensor_id(hass, mock_entry, "week_status")).state == expected


async def test_week_status_attributes(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """Counts, week start, and tonight's label."""
    await setup_with(hass, mock_entry, aioclient_mock, lists_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "week_status"))
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Week status"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["not_started", "partial", "planned"]
    assert {
        k: state.attributes[k]
        for k in (
            "nights_planned",
            "nights_total",
            "shopping_week_start",
            "shopping_week_exists",
            "tonight",
        )
    } == {
        "nights_planned": 6,
        "nights_total": 7,
        "shopping_week_start": "2030-01-05",
        "shopping_week_exists": True,
        "tonight": "Roast chicken",
    }


async def test_week_status_tonight_unplanned(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    status_json: dict[str, Any],
) -> None:
    """Nothing tonight → tonight is None."""
    status_json["weekStatus"]["tonight"] = {
        "date": "2030-01-05",
        "planned": False,
        "kind": None,
        "label": None,
    }
    await setup_with(hass, mock_entry, aioclient_mock, lists_json, status_json)
    assert hass.states.get(sensor_id(hass, mock_entry, "week_status")).attributes["tonight"] is None


async def test_week_status_no_shopping_week(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """/status 409 (no shopping week yet) → not_started, shopping_week_exists false."""
    await setup_with(
        hass,
        mock_entry,
        aioclient_mock,
        lists_json,
        status={"error": "no_shopping_week"},
        status_kwargs={"status": 409},
    )
    state = hass.states.get(sensor_id(hass, mock_entry, "week_status"))
    assert state.state == "not_started"
    assert state.attributes["shopping_week_exists"] is False
    assert state.attributes["nights_planned"] == 0
    assert state.attributes["shopping_week_start"] == "2030-01-05"  # from /meals
    assert state.attributes["tonight"] is None


# ── Woolies order ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("app_status", "collection", "expected"),
    [
        ("not_started", "2030-01-05", "not_placed"),
        ("exported", "2030-01-05", "exported"),
        ("ordered", "2030-01-12", "placed"),  # collection another day
        ("ordered", "2030-01-05", "pickup_today"),  # collection is today (Perth)
    ],
)
async def test_woolies_states(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    status_json: dict[str, Any],
    app_status: str,
    collection: str,
    expected: str,
) -> None:
    """App status maps to the four sensor states."""
    status_json["woolworthsOrder"].update(status=app_status, collectionDate=collection)
    await setup_with(hass, mock_entry, aioclient_mock, lists_json, status_json)
    assert hass.states.get(sensor_id(hass, mock_entry, "woolies_order")).state == expected


async def test_pickup_today_turns_off_next_day(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    status_json: dict[str, Any],
) -> None:
    """pickup_today on collection day; placed again once 'today' moves on."""
    status_json["woolworthsOrder"].update(status="ordered", collectionDate="2030-01-05")
    await setup_with(hass, mock_entry, aioclient_mock, lists_json, status_json)
    entity_id = sensor_id(hass, mock_entry, "woolies_order")
    assert hass.states.get(entity_id).state == "pickup_today"

    status_json["today"] = "2030-01-06"
    mock_lists(aioclient_mock, lists_json, status_body=status_json)
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "placed"


async def test_exported_on_collection_day_stays_exported(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    status_json: dict[str, Any],
) -> None:
    """Exported on collection day is still exported (only a placed order is pickup_today)."""
    status_json["woolworthsOrder"].update(status="exported", collectionDate="2030-01-05")
    await setup_with(hass, mock_entry, aioclient_mock, lists_json, status_json)
    assert hass.states.get(sensor_id(hass, mock_entry, "woolies_order")).state == "exported"


async def test_woolies_attributes(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """Dates, counts and week start; enum with the four options."""
    await setup_with(hass, mock_entry, aioclient_mock, lists_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "woolies_order"))
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Woolies order"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["not_placed", "exported", "placed", "pickup_today"]
    assert {
        k: state.attributes[k]
        for k in ("order_date", "collection_date", "item_count", "items_remaining", "week_start")
    } == {
        "order_date": "2030-01-04",
        "collection_date": "2030-01-05",
        "item_count": 4,
        "items_remaining": 1,
        "week_start": "2030-01-05",
    }


async def test_woolies_unavailable_without_shopping_week(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """/status 409 → Woolies order unavailable."""
    await setup_with(
        hass,
        mock_entry,
        aioclient_mock,
        lists_json,
        status={"error": "no_shopping_week"},
        status_kwargs={"status": 409},
    )
    assert hass.states.get(sensor_id(hass, mock_entry, "woolies_order")).state == STATE_UNAVAILABLE


async def test_state_names_translated(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Every option of both sensors has an English name."""
    t = await async_get_translations(hass, "en", "entity", [DOMAIN])
    prefix = f"component.{DOMAIN}.entity.sensor"
    assert {
        s: t[f"{prefix}.woolies_order.state.{s}"]
        for s in ("not_placed", "exported", "placed", "pickup_today")
    } == {
        "not_placed": "Not placed",
        "exported": "Exported",
        "placed": "Placed",
        "pickup_today": "Pickup today",
    }
    assert {
        s: t[f"{prefix}.week_status.state.{s}"] for s in ("not_started", "partial", "planned")
    } == {
        "not_started": "Not started",
        "partial": "Partly planned",
        "planned": "Planned",
    }
