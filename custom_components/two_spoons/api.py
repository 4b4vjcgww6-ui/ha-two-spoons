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


@dataclass(frozen=True, slots=True)
class MealNight:
    """One night from GET /meals. Unplanned nights have kind and label None.

    The ready-meal details (name, cook method, oven temp, cook time, note)
    are filled in after shopping and are None until then, and on other
    kinds of night. `label` is the name once it's set.
    """

    date: str
    week_start: str
    planned: bool
    kind: str | None
    label: str | None
    name: str | None
    cook_method: str | None
    oven_temp_c: int | None
    cook_mins: int | None
    note: str | None
    bought: bool  # the night's Ready Meals item is ticked
    details_needed: bool  # today, or later and bought, with details missing


@dataclass(frozen=True, slots=True)
class MealBaking:
    """A week's weekend baking slot."""

    week_start: str
    label: str


@dataclass(frozen=True, slots=True)
class Meals:
    """GET /meals: 14 nights from the eating week's Saturday, plus baking."""

    today: str
    timezone: str
    shopping_week_start: str
    version: str
    nights: tuple[MealNight, ...]
    baking: tuple[MealBaking, ...]


@dataclass(frozen=True, slots=True)
class WeekStatus:
    """GET /status → weekStatus (counts the shopping week)."""

    shopping_week_start: str
    nights_planned: int
    nights_total: int
    tonight: str | None


@dataclass(frozen=True, slots=True)
class WooliesOrder:
    """GET /status → woolworthsOrder (the shopping week's order)."""

    week_start: str
    status: str
    order_date: str | None
    collection_date: str | None
    item_count: int | None
    items_remaining: int | None


@dataclass(frozen=True, slots=True)
class Status:
    """The parts of GET /status the integration uses."""

    today: str
    week: WeekStatus
    woolies: WooliesOrder


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


def _parse_meals(body: Any) -> Meals:
    try:
        nights = tuple(
            MealNight(
                date=n["date"],
                week_start=n["weekStart"],
                planned=bool(n["planned"]),
                kind=n["kind"],
                label=n["label"],
                name=n["name"],
                cook_method=n["cookMethod"],
                oven_temp_c=n["ovenTempC"],
                cook_mins=n["cookMins"],
                note=n["note"],
                bought=bool(n["bought"]),
                details_needed=bool(n["detailsNeeded"]),
            )
            for n in body["nights"]
        )
        return Meals(
            today=body["today"],
            timezone=body["timezone"],
            shopping_week_start=body["shoppingWeekStart"],
            version=body["version"],
            nights=nights,
            baking=tuple(
                MealBaking(week_start=b["weekStart"], label=b["label"]) for b in body["baking"]
            ),
        )
    except (KeyError, TypeError) as err:
        raise TwoSpoonsConnectionError("unexpected /meals response") from err


def _parse_status(body: Any) -> Status:
    try:
        ws = body["weekStatus"]
        wo = body["woolworthsOrder"]
        return Status(
            today=body["today"],
            week=WeekStatus(
                shopping_week_start=ws["shoppingWeekStart"],
                nights_planned=int(ws["nightsPlanned"]),
                nights_total=int(ws["nightsTotal"]),
                tonight=(ws.get("tonight") or {}).get("label"),
            ),
            woolies=WooliesOrder(
                week_start=wo["weekStart"],
                status=wo["status"],
                order_date=wo["orderDate"],
                collection_date=wo["collectionDate"],
                item_count=wo["itemCount"],
                items_remaining=wo["itemsRemaining"],
            ),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise TwoSpoonsConnectionError("unexpected /status response") from err


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

    async def async_get_meals(self) -> Meals:
        """GET /meals: this week's and next week's nights."""
        return _parse_meals(await self._request("GET", "/meals"))

    async def async_get_week_status(self) -> Status:
        """GET /status, parsed (async_get_status returns the raw body)."""
        return _parse_status(await self._request("GET", "/status"))

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
