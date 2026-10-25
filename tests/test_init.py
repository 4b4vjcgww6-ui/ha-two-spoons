"""Setup, unload, polling failures and the two_spoons.refresh service."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import CONF_POLL_MINUTES, DOMAIN

from .conftest import mock_lists, todo_entity_id


async def test_setup_and_unload(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Loads, then unloads cleanly."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_user_agent_has_version(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """User-Agent is HomeAssistant-TwoSpoons/<manifest version>."""
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["User-Agent"] == "HomeAssistant-TwoSpoons/0.4.0"


async def test_setup_401_starts_reauth(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected token at startup fails setup and asks for a new token."""
    mock_lists(aioclient_mock, {"error": "unauthorized"}, status=401)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 500, "body": {"error": "internal_error"}},
        {"status": 403, "body": None, "headers": {"x-vercel-mitigated": "challenge"}},
    ],
)
async def test_setup_retry(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, Any],
) -> None:
    """Server errors and firewall blocks retry setup later."""
    body = kwargs.pop("body")
    mock_lists(aioclient_mock, body, **kwargs)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_no_shopping_week(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """409 no_shopping_week: entry stays loaded, lists go unavailable, then come back."""
    entity_id = todo_entity_id(hass, "GG")
    mock_lists(aioclient_mock, {"error": "no_shopping_week"}, status=409)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    mock_lists(aioclient_mock, lists_json)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "1"


async def test_setup_with_no_shopping_week(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Starting with no shopping week still loads (no lists yet)."""
    mock_lists(aioclient_mock, {"error": "no_shopping_week"}, status=409)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED
    assert hass.states.async_entity_ids("todo") == []


async def test_poll_interval(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Polls at the configured interval (default 5 minutes)."""
    hass.config_entries.async_update_entry(mock_entry, options={CONF_POLL_MINUTES: 3})
    mock_lists(aioclient_mock, lists_json)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 3  # /lists, /meals, /status

    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 3

    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 6


async def test_default_poll_interval(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Five minutes when no option is set."""
    assert setup_integration.runtime_data.update_interval == timedelta(minutes=5)


async def test_refresh_service(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """two_spoons.refresh fetches straight away and updates the lists."""
    entity_id = todo_entity_id(hass, "BK")
    lists_json["stores"][2]["items"].append(
        {
            "id": "00000000-0000-4000-8000-000000000302",
            "summary": "Bagels — 4",
            "status": "needs_action",
            "description": "Bakery",
        }
    )
    mock_lists(aioclient_mock, lists_json)
    await hass.services.async_call(DOMAIN, "refresh", blocking=True)
    assert aioclient_mock.call_count == 3
    assert hass.states.get(entity_id).state == "2"


async def test_refresh_service_failure(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failed refresh is reported to the caller."""
    mock_lists(aioclient_mock, {"error": "internal_error"}, status=500)
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(DOMAIN, "refresh", blocking=True)
    assert exc.value.translation_key == "refresh_failed"


async def test_refresh_service_not_loaded(hass: HomeAssistant) -> None:
    """With nothing loaded, the service says so."""
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(DOMAIN, "refresh", blocking=True)
    assert exc.value.translation_key == "not_loaded"


async def test_refresh_service_registered_once(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The service exists independently of entries (registered in async_setup)."""
    assert hass.services.has_service(DOMAIN, "refresh")
    await hass.config_entries.async_unload(setup_integration.entry_id)
    assert hass.services.has_service(DOMAIN, "refresh")


async def test_poll_401_starts_reauth(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token revoked while running triggers reauth."""
    mock_lists(aioclient_mock, {"error": "unauthorized"}, status=401)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
