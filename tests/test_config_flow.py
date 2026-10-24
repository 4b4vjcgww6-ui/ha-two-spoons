"""Config, reauth, reconfigure and options flows."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import CONF_API_TOKEN, CONF_POLL_MINUTES, DOMAIN

from .conftest import API, BASE, TOKEN, mock_lists

NEW_TOKEN = "fedcba9876543210" * 4
OTHER = "https://other.example.com"

FAILURES = [
    ({"status": 401, "json": {"error": "unauthorized"}}, "invalid_auth"),
    (
        {"status": 403, "text": "challenge", "headers": {"x-vercel-mitigated": "challenge"}},
        "blocked_by_firewall",
    ),
    ({"status": 500, "json": {"error": "internal_error"}}, "cannot_connect"),
    ({"exc": TimeoutError()}, "cannot_connect"),
    ({"exc": RuntimeError("boom")}, "unknown"),
]


def mock_status(aioclient_mock: AiohttpClientMocker, base: str = BASE, **kwargs: Any) -> None:
    """Make GET /status answer with this (replacing every registered mock)."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{base}/api/ha/v1/status", **kwargs)


async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status_json: dict[str, Any]
) -> None:
    """URL + token, checked with GET /status, create an entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    mock_status(aioclient_mock, json=status_json)
    # Setting up the new entry fetches /lists.
    aioclient_mock.get(
        f"{API}/lists",
        json={"weekId": "w", "weekStart": "2030-01-05", "version": "v", "stores": []},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: f"{BASE}/api/ha/v1/", CONF_API_TOKEN: f"  {TOKEN}\n"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "example.com"
    assert result["data"] == {CONF_URL: BASE, CONF_API_TOKEN: TOKEN}
    assert result["result"].unique_id == BASE
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == f"{API}/status"
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_user_flow_accepts_409(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No shopping week still proves the token works."""
    mock_status(aioclient_mock, status=409, json={"error": "no_shopping_week"})
    aioclient_mock.get(f"{API}/lists", status=409, json={"error": "no_shopping_week"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE, CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(("kwargs", "error"), FAILURES)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, Any],
    error: str,
    status_json: dict[str, Any],
) -> None:
    """Each failure shows its own error; fixing it completes the flow."""
    mock_status(aioclient_mock, **kwargs)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE, CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_status(aioclient_mock, json=status_json)
    aioclient_mock.get(
        f"{API}/lists",
        json={"weekId": "w", "weekStart": "2030-01-05", "version": "v", "stores": []},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE, CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-URL is caught before any request."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "example.com", CONF_API_TOKEN: TOKEN}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert aioclient_mock.call_count == 0


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same site can't be added twice."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: f"{BASE}/", CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert aioclient_mock.call_count == 0


async def test_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    status_json: dict[str, Any],
    lists_json: dict[str, Any],
) -> None:
    """A rejected token, then a new one is saved and the entry reloads."""
    entry = setup_integration
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["host"] == "example.com"

    mock_status(aioclient_mock, status=401, json={"error": "unauthorized"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: NEW_TOKEN}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_status(aioclient_mock, json=status_json)
    aioclient_mock.get(f"{API}/lists", json=lists_json)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: NEW_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_URL: BASE, CONF_API_TOKEN: NEW_TOKEN}


async def test_reconfigure_url_keeps_token(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    status_json: dict[str, Any],
    lists_json: dict[str, Any],
) -> None:
    """A new URL with a blank token keeps the current token."""
    entry = setup_integration
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    mock_status(aioclient_mock, OTHER, json=status_json)
    aioclient_mock.get(f"{OTHER}/api/ha/v1/lists", json=lists_json)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: f"{OTHER}/"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_URL: OTHER, CONF_API_TOKEN: TOKEN}
    assert entry.unique_id == OTHER
    assert entry.title == "other.example.com"
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_reconfigure_token(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    status_json: dict[str, Any],
    lists_json: dict[str, Any],
) -> None:
    """Same URL, new token; an error first is shown on the form."""
    entry = setup_integration
    result = await entry.start_reconfigure_flow(hass)

    mock_status(aioclient_mock, status=401, json={"error": "unauthorized"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE, CONF_API_TOKEN: NEW_TOKEN}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_status(aioclient_mock, json=status_json)
    aioclient_mock.get(f"{API}/lists", json=lists_json)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE, CONF_API_TOKEN: NEW_TOKEN}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_URL: BASE, CONF_API_TOKEN: NEW_TOKEN}


async def test_reconfigure_to_another_entrys_url(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Can't point one entry at a site another entry already uses."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=OTHER, data={CONF_URL: OTHER, CONF_API_TOKEN: TOKEN}
    ).add_to_hass(hass)
    result = await setup_integration.start_reconfigure_flow(hass)
    aioclient_mock.clear_requests()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: OTHER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert aioclient_mock.call_count == 0


async def test_reconfigure_invalid_url(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A non-URL stays on the form."""
    result = await setup_integration.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "nope"})
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_options_flow(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """The polling interval is saved and applied after the automatic reload."""
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    mock_lists(aioclient_mock, lists_json)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_MINUTES: 12}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_POLL_MINUTES: 12}
    assert entry.runtime_data.update_interval.total_seconds() == 12 * 60


@pytest.mark.parametrize("minutes", [1, 61])
async def test_options_flow_bounds(
    hass: HomeAssistant, setup_integration: MockConfigEntry, minutes: int
) -> None:
    """Below 2 or above 60 minutes is refused."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    with pytest.raises(Exception):  # noqa: B017 - voluptuous's Invalid, wrapped by the flow manager
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_POLL_MINUTES: minutes}
        )
