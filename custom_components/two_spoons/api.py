"""Client for the Two Spoons HA API.

No Home Assistant imports here: the client takes an aiohttp session and
raises its own exceptions, which the coordinator and entities translate.
The token is only ever sent as a header and never logged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import API_PATH, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class TwoSpoonsError(Exception):
    """Base error for the Two Spoons API."""


class TwoSpoonsConnectionError(TwoSpoonsError):
    """Network failure, timeout, server error or unreadable response."""


class TwoSpoonsAuthError(TwoSpoonsError):
    """The token was rejected (HTTP 401)."""


class TwoSpoonsFirewallError(TwoSpoonsError):
    """Vercel's firewall blocked the request (HTTP 403 + x-vercel-mitigated)."""


class TwoSpoonsNoShoppingWeekError(TwoSpoonsError):
    """There is no shopping week right now (HTTP 409 no_shopping_week)."""


class TwoSpoonsApiError(TwoSpoonsError):
    """Any other error the API reports as {"error": code}."""

    def __init__(self, status: int, code: str) -> None:
        """Keep the HTTP status and the API's error code."""
        super().__init__(f"HTTP {status} {code}")
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class ListItem:
    """One item on a store's list, as GET /lists returns it."""

    id: str
    summary: str
    completed: bool
    description: str


@dataclass(frozen=True, slots=True)
class StoreList:
    """One store's list."""

    code: str
    name: str
    items: tuple[ListItem, ...]


@dataclass(frozen=True, slots=True)
class ShoppingLists:
    """Everything GET /lists returns, keyed by store code in app order."""

    week_id: str
    week_start: str
    version: str
    stores: dict[str, StoreList]


def normalize_base_url(raw: str) -> str:
    """Return the site's base URL from what the user typed.

    Accepts the site address or the full API URL, with or without a
    trailing slash. Raises ValueError if it isn't an http(s) URL with a host.
    """
    url = URL(raw.strip())
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError("not an http(s) URL")
    path = url.path.rstrip("/")
    if path.endswith(API_PATH):
        path = path[: -len(API_PATH)]
    return str(url.with_path(path or "/").with_query(None).with_fragment(None)).rstrip("/")


def _parse_lists(body: Any) -> ShoppingLists:
    try:
        stores = {
            store["code"]: StoreList(
                code=store["code"],
                name=store["name"],
                items=tuple(
                    ListItem(
                        id=item["id"],
                        summary=item["summary"],
                        completed=item["status"] == "completed",
                        description=item.get("description") or "",
                    )
                    for item in store["items"]
                ),
            )
            for store in body["stores"]
        }
        return ShoppingLists(
            week_id=body["weekId"],
            week_start=body["weekStart"],
            version=body["version"],
            stores=stores,
        )
    except (KeyError, TypeError) as err:
        raise TwoSpoonsConnectionError("unexpected /lists response") from err


class TwoSpoonsClient:
    """Talks to /api/ha/v1 with a bearer token."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str, user_agent: str
    ) -> None:
        """Store the connection details; nothing is sent yet."""
        self._session = session
        self._api = base_url.rstrip("/") + API_PATH
        self._token = token
        self._user_agent = user_agent

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, self._api + path, headers=headers, json=json
                ) as resp:
                    status = resp.status
                    mitigated = resp.headers.get("x-vercel-mitigated")
                    try:
                        body = await resp.json(content_type=None)
                    except ValueError:
                        body = None
        except TimeoutError as err:
            _LOGGER.debug("%s %s timed out", method, path)
            raise TwoSpoonsConnectionError("timeout") from err
        except aiohttp.ClientError as err:
            _LOGGER.debug("%s %s failed: %s", method, path, type(err).__name__)
            raise TwoSpoonsConnectionError(type(err).__name__) from err

        _LOGGER.debug("%s %s -> HTTP %s", method, path, status)
        if 200 <= status < 300:
            return body

        code = body.get("error") if isinstance(body, dict) else None
        if status == 401:
            raise TwoSpoonsAuthError
        if status == 403 and mitigated:
            raise TwoSpoonsFirewallError
        if status == 409 and code == "no_shopping_week":
            raise TwoSpoonsNoShoppingWeekError
        if status >= 500 or not isinstance(code, str):
            raise TwoSpoonsConnectionError(f"HTTP {status}")
        raise TwoSpoonsApiError(status, code)

    async def async_get_status(self) -> dict[str, Any]:
        """GET /status. Used to check the URL and token."""
        return await self._request("GET", "/status")

    async def async_get_lists(self) -> ShoppingLists:
        """GET /lists: one list per active store for the shopping week."""
        return _parse_lists(await self._request("GET", "/lists"))

    async def async_create_item(self, store_code: str, text: str) -> None:
        """POST /items into one store (may merge into an existing item)."""
        await self._request("POST", "/items", {"text": text, "store": store_code})

    async def async_update_item(self, item_id: str, *, completed: bool, text: str) -> None:
        """PATCH /items/:id. The API treats an unchanged text as no rename."""
        await self._request(
            "PATCH",
            f"/items/{item_id}",
            {"status": "completed" if completed else "needs_action", "text": text},
        )

    async def async_delete_item(self, item_id: str) -> None:
        """DELETE /items/:id."""
        await self._request("DELETE", f"/items/{item_id}")
