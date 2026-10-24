"""The Meals calendar."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import DOMAIN

from .conftest import mock_lists

PERTH = ZoneInfo("Australia/Perth")


def calendar_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The Meals calendar's entity id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "calendar", DOMAIN, f"{entry.entry_id}_meals"
    )
    assert entity_id is not None
    return entity_id


async def setup_at(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
    when: datetime,
    meals: dict[str, Any] | None = None,
) -> str:
    """Set up at a given Perth time with Home Assistant also on Perth time."""
    await hass.config.async_set_time_zone("Australia/Perth")
    freezer.move_to(when)
    mock_lists(aioclient_mock, lists_json, meals_body=meals)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return calendar_id(hass, entry)


async def get_events(
    hass: HomeAssistant, entity_id: str, start: str, end: str
) -> list[dict[str, Any]]:
    """calendar.get_events over a date range."""
    resp = await hass.services.async_call(
        "calendar",
        "get_events",
        {"start_date_time": start, "end_date_time": end},
        target={ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )
    return resp[entity_id]["events"]


async def test_tonight_is_the_state(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """On a planned night the calendar is on, showing tonight's dinner."""
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 18, 0, tzinfo=PERTH),
    )
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Meals"
    assert state.attributes["message"] == "Roast chicken"
    assert state.attributes["all_day"] is True
    assert state.attributes["description"] == "kind: recipe"
    assert state.attributes["start_time"] == "2030-01-05 00:00:00"
    assert state.attributes["end_time"] == "2030-01-06 00:00:00"


async def test_unplanned_tonight_is_off(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """Sunday has nothing planned: off, and no event (baking doesn't count)."""
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 6, 18, 0, tzinfo=PERTH),
    )
    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    assert "message" not in state.attributes


async def test_tonight_follows_perth_not_home_assistant_timezone(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """With HA on UTC, 17:00 UTC Saturday is already Sunday in Perth."""
    freezer.move_to(datetime(2030, 1, 5, 17, 0, tzinfo=ZoneInfo("UTC")))
    await hass.config.async_set_time_zone("UTC")
    mock_lists(aioclient_mock, lists_json)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(calendar_id(hass, mock_entry)).state == STATE_OFF


async def test_events_in_window(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """One all-day event per planned night, plus baking across Saturday and Sunday."""
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 9, 0, tzinfo=PERTH),
    )
    events = await get_events(
        hass, entity_id, "2030-01-05T00:00:00+08:00", "2030-01-19T00:00:00+08:00"
    )
    assert [(e["start"], e["end"], e["summary"]) for e in events] == [
        ("2030-01-05", "2030-01-06", "Roast chicken"),
        ("2030-01-05", "2030-01-07", "Baking: Lemon cake"),
        ("2030-01-07", "2030-01-08", "Ready Meal"),
        ("2030-01-08", "2030-01-09", "Ready Meal"),
        ("2030-01-09", "2030-01-10", "Ready Meal"),
        ("2030-01-10", "2030-01-11", "Ready Meal"),
        ("2030-01-11", "2030-01-12", "Takeaway"),
        ("2030-01-14", "2030-01-15", "Dinner with friends"),
    ]
    by_summary = {e["summary"]: e for e in events}
    assert by_summary["Ready Meal"]["description"] == "kind: ready_meal"
    assert by_summary["Dinner with friends"]["description"] == "kind: other"
    assert "description" not in by_summary["Baking: Lemon cake"]


async def test_events_range_filter(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """Only events overlapping the requested range come back."""
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 9, 0, tzinfo=PERTH),
    )
    events = await get_events(
        hass, entity_id, "2030-01-06T00:00:00+08:00", "2030-01-08T00:00:00+08:00"
    )
    assert [e["summary"] for e in events] == ["Baking: Lemon cake", "Ready Meal"]


async def test_method_and_prep_lines(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """Description shows only the lines that have values, in a fixed order."""
    meals_json["nights"][0]["recipe"]["method"] = "slow_cooker"
    meals_json["nights"][0]["recipe"]["prepNote"] = "Chicken out of the freezer"
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 18, 0, tzinfo=PERTH),
        meals=meals_json,
    )
    assert hass.states.get(entity_id).attributes["description"] == (
        "kind: recipe\nmethod: slow_cooker\nprep: Chicken out of the freezer"
    )

    meals_json["nights"][0]["recipe"]["method"] = None
    mock_lists(aioclient_mock, lists_json, meals_body=meals_json)
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["description"] == (
        "kind: recipe\nprep: Chicken out of the freezer"
    )


async def test_next_week_not_created(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """A week that doesn't exist yet (weekId null, nothing planned) adds no events."""
    for night in meals_json["nights"][7:]:
        night.update(weekId=None, planned=False, kind=None, label=None, recipe=None)
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 9, 0, tzinfo=PERTH),
        meals=meals_json,
    )
    events = await get_events(
        hass, entity_id, "2030-01-12T00:00:00+08:00", "2030-01-19T00:00:00+08:00"
    )
    assert events == []


async def test_no_baking(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
    meals_json: dict[str, Any],
) -> None:
    """Without a baking slot there's no baking event."""
    meals_json["baking"] = []
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 9, 0, tzinfo=PERTH),
        meals=meals_json,
    )
    events = await get_events(
        hass, entity_id, "2030-01-05T00:00:00+08:00", "2030-01-08T00:00:00+08:00"
    )
    assert [e["summary"] for e in events] == ["Roast chicken", "Ready Meal"]


async def test_read_only(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """No create/update/delete support."""
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 9, 0, tzinfo=PERTH),
    )
    assert hass.states.get(entity_id).attributes.get("supported_features", 0) == 0
    with pytest.raises(Exception):  # noqa: B017 - HA raises its "not supported" error
        await hass.services.async_call(
            "calendar",
            "create_event",
            {"summary": "x", "start_date": "2030-01-06", "end_date": "2030-01-07"},
            target={ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )


async def test_unavailable_when_meals_fail(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    lists_json: dict[str, Any],
) -> None:
    """A failed poll makes the calendar unavailable."""
    entity_id = await setup_at(
        hass,
        mock_entry,
        aioclient_mock,
        freezer,
        lists_json,
        datetime(2030, 1, 5, 9, 0, tzinfo=PERTH),
    )
    mock_lists(aioclient_mock, lists_json, meals_kwargs={"status": 500})
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
