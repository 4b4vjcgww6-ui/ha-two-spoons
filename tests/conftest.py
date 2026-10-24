"""Shared fixtures. Every URL, token and item here is invented."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import CONF_API_TOKEN, DOMAIN

BASE = "https://example.com"
API = f"{BASE}/api/ha/v1"
TOKEN = "0123456789abcdef" * 4  # fake, shaped like a real token

_FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """A fresh copy of a JSON fixture."""
    return copy.deepcopy(json.loads((_FIXTURES / name).read_text()))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def lists_json() -> dict[str, Any]:
    """GET /lists response."""
    return load_fixture("lists.json")


@pytest.fixture
def meals_json() -> dict[str, Any]:
    """GET /meals response."""
    return load_fixture("meals.json")


@pytest.fixture
def status_json() -> dict[str, Any]:
    """GET /status response."""
    return load_fixture("status.json")


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured entry, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="example.com",
        unique_id=BASE,
        data={CONF_URL: BASE, CONF_API_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    return entry


def mock_lists(
    aioclient_mock: AiohttpClientMocker,
    body: Any = None,
    *,
    meals_body: Any = None,
    status_body: Any = None,
    meals_kwargs: dict[str, Any] | None = None,
    status_kwargs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Make a poll answer with these (replacing every registered mock).

    GET /lists answers with `body`/kwargs; /meals and /status answer with
    the fixtures unless overridden.
    """
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/lists", json=body, **kwargs)
    aioclient_mock.get(
        f"{API}/meals",
        json=load_fixture("meals.json") if meals_body is None else meals_body,
        **(meals_kwargs or {}),
    )
    aioclient_mock.get(
        f"{API}/status",
        json=load_fixture("status.json") if status_body is None else status_body,
        **(status_kwargs or {}),
    )


# The requests one poll makes, in order.
POLL = [("get", "/lists", None), ("get", "/meals", None), ("get", "/status", None)]


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> MockConfigEntry:
    """The integration set up against the lists fixture."""
    mock_lists(aioclient_mock, lists_json)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    return mock_entry


def todo_entity_id(hass: HomeAssistant, code: str) -> str:
    """The entity id of a store's list, whatever naming scheme HA used."""
    entity_id = er.async_get(hass).async_get_entity_id("todo", DOMAIN, f"{DOMAIN}_{code}")
    assert entity_id is not None, code
    return entity_id
