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


async def setup_meals(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Set up with a given /meals body (or meals kwargs)."""
    mock_lists(aioclient_mock, lists_json, meals_body=meals, **kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


TONIGHT_KEYS = {
    "kind",
    "cook_method",
    "oven_temp_c",
    "cook_mins",
    "note",
    "bought",
    "details_needed",
    "date",
}

LASAGNE = {
    "label": "Beef lasagne",
    "name": "Beef lasagne",
    "cookMethod": "oven",
    "ovenTempC": 180,
    "cookMins": 45,
    "note": "Foil on",
    "bought": True,
}


# ── Tonight ──────────────────────────────────────────────────────────────────


async def test_tonight_recipe_night(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A recipe night: its label, kind recipe, no ready-meal details."""
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json)
    entity_id = sensor_id(hass, mock_entry, "tonight")
    assert entity_id == "sensor.two_spoons_tonight"
    state = hass.states.get(entity_id)
    assert state.state == "Roast chicken"
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Tonight"
    assert "device_class" not in state.attributes
    assert {k: state.attributes[k] for k in TONIGHT_KEYS} == {
        "kind": "recipe",
        "cook_method": None,
        "oven_temp_c": None,
        "cook_mins": None,
        "note": None,
        "bought": False,
        "details_needed": False,
        "date": "2030-01-05",
    }


async def test_tonight_ready_meal_with_details(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """A filled-in ready meal tonight: the name, and every detail as attributes."""
    meals_json["today"] = "2030-01-07"
    meals_json["nights"][2].update(LASAGNE)
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json, meals_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "tonight"))
    assert state.state == "Beef lasagne"
    assert {k: state.attributes[k] for k in TONIGHT_KEYS} == {
        "kind": "ready_meal",
        "cook_method": "oven",
        "oven_temp_c": 180,
        "cook_mins": 45,
        "note": "Foil on",
        "bought": True,
        "details_needed": False,
        "date": "2030-01-07",
    }


async def test_tonight_ready_meal_needing_details(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """Bought, no details yet: "Ready Meal", details_needed true."""
    meals_json["today"] = "2030-01-08"
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json, meals_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "tonight"))
    assert state.state == "Ready Meal"
    assert state.attributes["kind"] == "ready_meal"
    assert state.attributes["cook_method"] is None
    assert state.attributes["bought"] is True
    assert state.attributes["details_needed"] is True


@pytest.mark.parametrize(
    "today",
    [
        "2030-01-06",  # in the window, nothing planned
        "2030-01-19",  # past the window's last night
        "2030-01-04",  # before its first night
    ],
)
async def test_tonight_not_planned(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
    today: str,
) -> None:
    """No planned night tonight → "Not planned", empty details, flags false."""
    meals_json["today"] = today
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json, meals_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "tonight"))
    assert state.state == "Not planned"
    assert {k: state.attributes[k] for k in TONIGHT_KEYS} == {
        "kind": None,
        "cook_method": None,
        "oven_temp_c": None,
        "cook_mins": None,
        "note": None,
        "bought": False,
        "details_needed": False,
        "date": today,
    }


async def test_tonight_follows_the_apps_today(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """When /meals moves on to the next day, the next poll shows that night."""
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json, meals_json)
    entity_id = sensor_id(hass, mock_entry, "tonight")
    assert hass.states.get(entity_id).state == "Roast chicken"

    meals_json["today"] = "2030-01-06"
    mock_lists(aioclient_mock, lists_json, meals_body=meals_json)
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "Not planned"


# ── Ready meals needing details ──────────────────────────────────────────────


async def test_needing_details_from_fixture(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """One bought night without details → 1, listed with its date and label."""
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json)
    entity_id = sensor_id(hass, mock_entry, "ready_meals_needing_details")
    assert entity_id == "sensor.two_spoons_ready_meals_needing_details"
    state = hass.states.get(entity_id)
    assert state.state == "1"
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Ready meals needing details"
    assert state.attributes["nights"] == [{"date": "2030-01-08", "label": "Ready Meal"}]


async def test_needing_details_none(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """No night flagged → 0 and an empty list."""
    for night in meals_json["nights"]:
        night["detailsNeeded"] = False
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json, meals_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "ready_meals_needing_details"))
    assert state.state == "0"
    assert state.attributes["nights"] == []


async def test_needing_details_counts_only_flagged_nights_in_date_order(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """The count is the app's detailsNeeded flags (bought alone doesn't count)."""
    nights = meals_json["nights"]
    nights[2].update(LASAGNE, detailsNeeded=False)  # filled in
    nights[3].update(detailsNeeded=True)  # bought, empty
    nights[4].update(label="Pork dumplings", name="Pork dumplings", detailsNeeded=True)  # no method
    nights[5].update(bought=True, detailsNeeded=False)
    meals_json["nights"] = list(reversed(nights))  # order comes from the dates
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json, meals_json)
    state = hass.states.get(sensor_id(hass, mock_entry, "ready_meals_needing_details"))
    assert state.state == "2"
    assert state.attributes["nights"] == [
        {"date": "2030-01-08", "label": "Ready Meal"},
        {"date": "2030-01-09", "label": "Pork dumplings"},
    ]


# ── Both meals sensors without /meals ────────────────────────────────────────


@pytest.mark.parametrize("key", ["tonight", "ready_meals_needing_details"])
async def test_meals_sensors_unavailable_on_409(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    key: str,
) -> None:
    """/meals 409 (no shopping week) → unavailable; the other sensors still work."""
    await setup_meals(
        hass,
        mock_entry,
        aioclient_mock,
        lists_json,
        meals={"error": "no_shopping_week"},
        meals_kwargs={"status": 409},
    )
    assert hass.states.get(sensor_id(hass, mock_entry, key)).state == STATE_UNAVAILABLE
    assert hass.states.get(sensor_id(hass, mock_entry, "week_status")).state != STATE_UNAVAILABLE


@pytest.mark.parametrize("key", ["tonight", "ready_meals_needing_details"])
async def test_meals_sensors_unavailable_when_poll_fails(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    key: str,
) -> None:
    """A failed poll makes them unavailable, and the next good one brings them back."""
    await setup_meals(hass, mock_entry, aioclient_mock, lists_json)
    entity_id = sensor_id(hass, mock_entry, key)
    mock_lists(aioclient_mock, lists_json, meals_kwargs={"status": 500})
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    mock_lists(aioclient_mock, lists_json)
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


async def test_meals_sensor_names_translated(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Both new sensors and their attributes have English names."""
    t = await async_get_translations(hass, "en", "entity", [DOMAIN])
    prefix = f"component.{DOMAIN}.entity.sensor"
    assert t[f"{prefix}.tonight.name"] == "Tonight"
    assert t[f"{prefix}.ready_meals_needing_details.name"] == "Ready meals needing details"
    assert all(f"{prefix}.tonight.state_attributes.{k}.name" in t for k in TONIGHT_KEYS)
    assert f"{prefix}.ready_meals_needing_details.state_attributes.nights.name" in t
