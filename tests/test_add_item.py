"""The two_spoons.add_item action. Every item and URL here is invented."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import CONF_API_TOKEN, DOMAIN

from .conftest import API, POLL, TOKEN, load_fixture, mock_lists

OAT_MILK = "00000000-0000-4000-8000-000000000201"


def added(summary: str, code: str, name: str, *, merged: bool = False) -> dict[str, Any]:
    """A POST /items response."""
    body: dict[str, Any] = {
        "merged": merged,
        "item": {
            "id": OAT_MILK if merged else "00000000-0000-4000-8000-000000000999",
            "summary": summary,
            "status": "needs_action",
            "description": "Dairy",
        },
        "store": {"code": code, "name": name},
    }
    if merged:
        body["mergedInto"] = OAT_MILK
    return body


async def add_item(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any] | None:
    """Call the action, asking for its response."""
    return await hass.services.async_call(
        DOMAIN, "add_item", data, blocking=True, return_response=True
    )


def requests(aioclient_mock: AiohttpClientMocker) -> list[tuple[str, str, Any]]:
    """(method, path, json) for every request made, in order."""
    return [
        (method.lower(), str(url).removeprefix(API), body)
        for method, url, body, _ in aioclient_mock.mock_calls
    ]


async def test_no_store_lets_the_api_choose(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """With no store, only the text is sent; the response names the store chosen."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", status=201, json=added("Milk — 2 L", "WW", "Woolies"))
    response = await add_item(hass, {"item": "  2 litres of milk "})
    assert requests(aioclient_mock) == [("post", "/items", {"text": "2 litres of milk"}), *POLL]
    assert response == {
        "summary": "Milk — 2 L",
        "store_code": "WW",
        "store_name": "Woolies",
        "merged": False,
        "merged_into_summary": None,
    }


async def test_given_store(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A store code is sent upper-cased, and checked against /lists first."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", status=201, json=added("Pears — 4", "GG", "Green Grocer"))
    response = await add_item(hass, {"item": "4 pears", "store": " gg "})
    assert requests(aioclient_mock) == [
        ("post", "/items", {"text": "4 pears", "store": "GG"}),
        *POLL,
    ]
    assert response is not None
    assert response["store_code"] == "GG"
    assert response["store_name"] == "Green Grocer"


async def test_without_response(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """The response is optional."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", status=201, json=added("Pears", "GG", "Green Grocer"))
    assert (
        await hass.services.async_call(DOMAIN, "add_item", {"item": "Pears"}, blocking=True) is None
    )
    assert aioclient_mock.call_count == 4


async def test_unknown_store(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A code not in /lists is refused before anything is sent."""
    aioclient_mock.clear_requests()
    with pytest.raises(ServiceValidationError) as exc:
        await add_item(hass, {"item": "Pears", "store": "zz"})
    assert exc.value.translation_key == "unknown_store"
    assert exc.value.translation_placeholders == {"store": "ZZ", "codes": "GG, WW, BK"}
    assert str(exc.value) == "Two Spoons has no store with the code ZZ. The codes are GG, WW, BK"
    assert aioclient_mock.call_count == 0


async def test_unknown_store_from_the_api(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A store that's gone since the last poll: the API's 400 says so."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", status=400, json={"error": "unknown_store"})
    with pytest.raises(ServiceValidationError) as exc:
        await add_item(hass, {"item": "Pears", "store": "GG"})
    assert exc.value.translation_key == "unknown_store_code"
    assert str(exc.value) == "Two Spoons has no store with the code GG"


async def test_merged(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A merge returns the existing item, as the list now shows it."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", json=added("Oat milk — 4 L", "WW", "Woolies", merged=True))
    response = await add_item(hass, {"item": "2 litres of oat milk"})
    assert response == {
        "summary": "Oat milk — 4 L",
        "store_code": "WW",
        "store_name": "Woolies",
        "merged": True,
        "merged_into_summary": "Oat milk — 4 L",
    }


@pytest.mark.parametrize("item", ["   ", "x" * 201])
async def test_invalid_item_not_sent(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    item: str,
) -> None:
    """Blank or over-long text is refused locally."""
    aioclient_mock.clear_requests()
    with pytest.raises(ServiceValidationError) as exc:
        await add_item(hass, {"item": item})
    assert exc.value.translation_key == "invalid_item"
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    ("kwargs", "error", "key", "message"),
    [
        (
            {"status": 400, "json": {"error": "invalid_body"}},
            ServiceValidationError,
            "invalid_item",
            "Two Spoons couldn't add that item. Try saying it again, more briefly",
        ),
        (
            {"status": 403, "text": "x", "headers": {"x-vercel-mitigated": "challenge"}},
            HomeAssistantError,
            "add_item_blocked_by_firewall",
            "Two Spoons is blocking Home Assistant for now, so nothing was added. Try again later",
        ),
        (
            {"status": 409, "json": {"error": "no_shopping_week"}},
            HomeAssistantError,
            "add_item_no_shopping_week",
            "Two Spoons has no shopping week right now, so nothing was added",
        ),
        (
            {"exc": TimeoutError()},
            HomeAssistantError,
            "add_item_timeout",
            "Two Spoons took too long to answer. The item may still have been added, "
            "so check the list before trying again",
        ),
        (
            {"status": 500, "json": {"error": "internal_error"}},
            HomeAssistantError,
            "add_item_cannot_connect",
            "Something went wrong talking to Two Spoons. "
            "Check the list to see whether the item was added",
        ),
        (
            {"status": 201, "json": {"merged": False}},
            HomeAssistantError,
            "add_item_cannot_connect",
            "Something went wrong talking to Two Spoons. "
            "Check the list to see whether the item was added",
        ),
        (
            {"status": 422, "json": {"error": "something_new"}},
            HomeAssistantError,
            "add_item_rejected",
            "Two Spoons couldn't add that item (something_new)",
        ),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    kwargs: dict[str, Any],
    error: type[HomeAssistantError],
    key: str,
    message: str,
) -> None:
    """Failures become translated errors that read well aloud, and still refresh."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", **kwargs)
    with pytest.raises(error) as exc:
        await add_item(hass, {"item": "Pears"})
    assert exc.value.translation_key == key
    assert str(exc.value) == message
    assert requests(aioclient_mock)[1:] == POLL


async def test_401_starts_reauth(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected token raises and asks for a new token."""
    mock_lists(aioclient_mock, {"error": "unauthorized"}, status=401)
    aioclient_mock.post(f"{API}/items", status=401, json={"error": "unauthorized"})
    with pytest.raises(HomeAssistantError) as exc:
        await add_item(hass, {"item": "Pears"})
    assert exc.value.translation_key == "add_item_invalid_auth"
    assert str(exc.value) == (
        "Two Spoons rejected Home Assistant's token, so nothing was added. "
        "Re-enter the token in Home Assistant"
    )
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_not_loaded(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """With nothing loaded, the action says so."""
    await hass.config_entries.async_unload(setup_integration.entry_id)
    with pytest.raises(ServiceValidationError) as exc:
        await add_item(hass, {"item": "Pears"})
    assert exc.value.translation_key == "not_loaded"


# ── More than one entry ──────────────────────────────────────────────────────

SECOND = "https://second.example.com"
SECOND_API = f"{SECOND}/api/ha/v1"


@pytest.fixture
async def two_entries(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> MockConfigEntry:
    """A second Two Spoons site, set up alongside the first."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.get(f"{SECOND_API}/lists", json=lists_json)
    aioclient_mock.get(f"{SECOND_API}/meals", json=load_fixture("meals.json"))
    aioclient_mock.get(f"{SECOND_API}/status", json=load_fixture("status.json"))
    second = MockConfigEntry(
        domain=DOMAIN,
        title="second.example.com",
        unique_id=SECOND,
        data={CONF_URL: SECOND, CONF_API_TOKEN: TOKEN},
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    return second


async def test_multiple_entries_need_an_id(
    hass: HomeAssistant, two_entries: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """With two entries and no config_entry_id, nothing is sent."""
    aioclient_mock.mock_calls.clear()
    with pytest.raises(ServiceValidationError) as exc:
        await add_item(hass, {"item": "Pears"})
    assert exc.value.translation_key == "choose_entry"
    assert aioclient_mock.call_count == 0


async def test_multiple_entries_with_id(
    hass: HomeAssistant, two_entries: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """config_entry_id picks the site the item goes to."""
    aioclient_mock.post(
        f"{SECOND_API}/items", status=201, json=added("Pears", "GG", "Green Grocer")
    )
    aioclient_mock.mock_calls.clear()
    response = await add_item(hass, {"item": "Pears", "config_entry_id": two_entries.entry_id})
    assert response is not None
    assert response["summary"] == "Pears"
    method, url, body, _ = aioclient_mock.mock_calls[0]
    assert (method.lower(), str(url), body) == ("post", f"{SECOND_API}/items", {"text": "Pears"})
    # Only the second site is refreshed afterwards.
    assert all(str(url).startswith(SECOND_API) for _, url, _, _ in aioclient_mock.mock_calls)


async def test_unknown_entry_id(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """An id that isn't a loaded Two Spoons entry is refused."""
    with pytest.raises(ServiceValidationError) as exc:
        await add_item(hass, {"item": "Pears", "config_entry_id": "not-an-entry"})
    assert exc.value.translation_key == "not_loaded"
