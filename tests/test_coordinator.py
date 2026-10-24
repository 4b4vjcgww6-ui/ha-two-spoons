"""Polling /lists, /meals and /status: order, 409s, auth and the firewall back-off."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import DOMAIN

from .conftest import API, POLL, mock_lists, todo_entity_id

FIREWALL = {"status": 403, "text": "challenge", "headers": {"x-vercel-mitigated": "challenge"}}


def entity(hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str) -> str:
    """Entity id by unique id suffix."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None
    return entity_id


def calls(aioclient_mock: AiohttpClientMocker) -> list[tuple[str, str, Any]]:
    """Requests made, as (method, path, body)."""
    return [(m.lower(), str(u).removeprefix(API), d) for m, u, d, _ in aioclient_mock.mock_calls]


async def test_poll_order(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each cycle fetches /lists, then /meals, then /status."""
    assert calls(aioclient_mock) == POLL


@pytest.mark.parametrize("failing", ["meals", "status"])
async def test_401_on_any_request_starts_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    failing: str,
) -> None:
    """A rejected token on /meals or /status asks for a new token."""
    mock_lists(aioclient_mock, lists_json, **{f"{failing}_kwargs": {"status": 401}})
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_lists_409_keeps_meals(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """No shopping week for /lists: lists unavailable, calendar and sensors carry on."""
    mock_lists(aioclient_mock, {"error": "no_shopping_week"}, status=409)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(todo_entity_id(hass, "GG")).state == STATE_UNAVAILABLE
    assert (
        hass.states.get(entity(hass, setup_integration, "calendar", "meals")).state
        != STATE_UNAVAILABLE
    )
    assert (
        hass.states.get(entity(hass, setup_integration, "sensor", "woolies_order")).state
        != STATE_UNAVAILABLE
    )


async def test_status_409_keeps_lists(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """No shopping week for /status: Woolies unavailable, lists unaffected."""
    mock_lists(
        aioclient_mock,
        lists_json,
        status_body={"error": "no_shopping_week"},
        status_kwargs={"status": 409},
    )
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity(hass, setup_integration, "sensor", "woolies_order")).state
        == STATE_UNAVAILABLE
    )
    assert hass.states.get(todo_entity_id(hass, "GG")).state == "1"


@pytest.mark.parametrize("failing", ["lists", "meals", "status"])
async def test_firewall_repair_and_backoff(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    failing: str,
) -> None:
    """A firewall block on any request raises a repair and waits 15 minutes; success clears it."""
    kwargs = {"status": 403, "headers": FIREWALL["headers"]} if failing == "lists" else {}
    other = {f"{failing}_kwargs": FIREWALL} if failing != "lists" else {}
    mock_lists(aioclient_mock, None if failing == "lists" else lists_json, **kwargs, **other)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "blocked_by_firewall") is not None
    assert hass.states.get(todo_entity_id(hass, "GG")).state == STATE_UNAVAILABLE

    # The normal 5-minute poll is skipped...
    mock_lists(aioclient_mock, lists_json)
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0

    # ...until the 15-minute back-off has passed.
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls(aioclient_mock) == POLL
    assert issues.async_get_issue(DOMAIN, "blocked_by_firewall") is None
    assert hass.states.get(todo_entity_id(hass, "GG")).state == "1"


async def test_firewall_issue_text(hass: HomeAssistant) -> None:
    """The repair issue has English text."""
    t = await async_get_translations(hass, "en", "issues", [DOMAIN])
    assert t[f"component.{DOMAIN}.issues.blocked_by_firewall.title"] == (
        "Two Spoons is blocked by the Vercel firewall"
    )
