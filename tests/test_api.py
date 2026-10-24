"""TwoSpoonsClient: URLs, headers, response parsing and error mapping."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.api import (
    TwoSpoonsApiError,
    TwoSpoonsAuthError,
    TwoSpoonsClient,
    TwoSpoonsConnectionError,
    TwoSpoonsFirewallError,
    TwoSpoonsNoShoppingWeekError,
    normalize_base_url,
)

from .conftest import API, BASE, TOKEN

ITEM = "00000000-0000-4000-8000-000000000101"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("https://example.com/", "https://example.com"),
        ("  https://Example.com  ", "https://example.com"),
        ("https://example.com/api/ha/v1", "https://example.com"),
        ("https://example.com/api/ha/v1/", "https://example.com"),
        ("https://example.com/app/api/ha/v1", "https://example.com/app"),
        ("http://192.168.1.10:3000", "http://192.168.1.10:3000"),
        ("https://example.com/?x=1#y", "https://example.com"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    """The site address or the full API URL both give the site address."""
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "example.com", "ftp://example.com", "https://"])
def test_normalize_base_url_rejects(raw: str) -> None:
    """Anything that isn't an http(s) URL with a host is refused."""
    with pytest.raises(ValueError):
        normalize_base_url(raw)


@pytest.fixture
async def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> TwoSpoonsClient:
    """A client on the mocked shared session."""
    return TwoSpoonsClient(
        async_get_clientsession(hass), BASE, TOKEN, "HomeAssistant-TwoSpoons/9.9.9"
    )


async def test_headers(
    client: TwoSpoonsClient, aioclient_mock: AiohttpClientMocker, status_json: dict[str, Any]
) -> None:
    """Bearer token and User-Agent go on every request."""
    aioclient_mock.get(f"{API}/status", json=status_json)
    assert await client.async_get_status() == status_json
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == f"{API}/status"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["User-Agent"] == "HomeAssistant-TwoSpoons/9.9.9"


async def test_get_lists(
    client: TwoSpoonsClient, aioclient_mock: AiohttpClientMocker, lists_json: dict[str, Any]
) -> None:
    """GET /lists is parsed into stores in API order."""
    aioclient_mock.get(f"{API}/lists", json=lists_json)
    lists = await client.async_get_lists()
    assert list(lists.stores) == ["GG", "WW", "BK"]
    assert lists.week_start == "2030-01-05"
    gg = lists.stores["GG"]
    assert gg.name == "Green Grocer"
    assert [(i.summary, i.completed, i.description) for i in gg.items] == [
        ("Apples — 6", False, "Fruit & Veg"),
        ("Spinach", True, "Fruit & Veg"),
    ]


async def test_get_lists_bad_shape(
    client: TwoSpoonsClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unreadable body is a connection error, not a crash."""
    aioclient_mock.get(f"{API}/lists", json={"stores": [{"code": "GG"}]})
    with pytest.raises(TwoSpoonsConnectionError):
        await client.async_get_lists()


async def test_write_bodies(client: TwoSpoonsClient, aioclient_mock: AiohttpClientMocker) -> None:
    """POST, PATCH and DELETE send what the API expects."""
    aioclient_mock.post(f"{API}/items", status=201, json={"merged": False})
    aioclient_mock.patch(f"{API}/items/{ITEM}", json={"item": {}})
    aioclient_mock.delete(f"{API}/items/{ITEM}", json={"deleted": True, "id": ITEM})

    await client.async_create_item("GG", "2 pears")
    await client.async_update_item(ITEM, completed=True, text="Apples — 6")
    await client.async_update_item(ITEM, completed=False, text="Apples — 8")
    await client.async_delete_item(ITEM)

    calls = [(m.lower(), str(u), d) for m, u, d, _ in aioclient_mock.mock_calls]
    assert calls == [
        ("post", f"{API}/items", {"text": "2 pears", "store": "GG"}),
        ("patch", f"{API}/items/{ITEM}", {"status": "completed", "text": "Apples — 6"}),
        ("patch", f"{API}/items/{ITEM}", {"status": "needs_action", "text": "Apples — 8"}),
        ("delete", f"{API}/items/{ITEM}", None),
    ]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"status": 401, "json": {"error": "unauthorized"}}, TwoSpoonsAuthError),
        (
            {
                "status": 403,
                "text": "<html>challenge</html>",
                "headers": {"x-vercel-mitigated": "challenge"},
            },
            TwoSpoonsFirewallError,
        ),
        ({"status": 403, "text": "Forbidden"}, TwoSpoonsConnectionError),
        ({"status": 409, "json": {"error": "no_shopping_week"}}, TwoSpoonsNoShoppingWeekError),
        ({"status": 500, "json": {"error": "internal_error"}}, TwoSpoonsConnectionError),
        ({"status": 502, "text": "Bad gateway"}, TwoSpoonsConnectionError),
        ({"status": 200, "text": "not json"}, None),
        ({"exc": TimeoutError()}, TwoSpoonsConnectionError),
        ({"exc": aiohttp.ClientConnectionError()}, TwoSpoonsConnectionError),
    ],
)
async def test_error_mapping(
    client: TwoSpoonsClient,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, Any],
    expected: type[Exception] | None,
) -> None:
    """Each failure becomes the matching exception."""
    aioclient_mock.get(f"{API}/status", **kwargs)
    if expected is None:
        assert await client.async_get_status() is None
        return
    with pytest.raises(expected):
        await client.async_get_status()


@pytest.mark.parametrize(
    ("status", "code"),
    [(422, "ready_meal_child_readonly"), (404, "not_found"), (400, "unknown_store")],
)
async def test_api_error_codes(
    client: TwoSpoonsClient, aioclient_mock: AiohttpClientMocker, status: int, code: str
) -> None:
    """Other API errors keep their status and code."""
    aioclient_mock.delete(f"{API}/items/{ITEM}", status=status, json={"error": code})
    with pytest.raises(TwoSpoonsApiError) as exc:
        await client.async_delete_item(ITEM)
    assert (exc.value.status, exc.value.code) == (status, code)


async def test_token_never_logged(
    client: TwoSpoonsClient,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    lists_json: dict[str, Any],
) -> None:
    """Debug logging shows method, path and status only."""
    caplog.set_level(logging.DEBUG, logger="custom_components.two_spoons")
    aioclient_mock.get(f"{API}/lists", json=lists_json)
    aioclient_mock.get(f"{API}/status", status=401, json={"error": "unauthorized"})
    await client.async_get_lists()
    with pytest.raises(TwoSpoonsAuthError):
        await client.async_get_status()
    assert "GET /lists -> HTTP 200" in caplog.text
    assert TOKEN not in caplog.text
    assert "Bearer" not in caplog.text
