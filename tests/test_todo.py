"""The per-store to-do lists: entities, items, writes and their failures."""

from __future__ import annotations

from typing import Any

from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.two_spoons.const import DOMAIN

from .conftest import API, mock_lists, todo_entity_id

APPLES = "00000000-0000-4000-8000-000000000101"
SPINACH = "00000000-0000-4000-8000-000000000102"
MONDAY = "00000000-0000-4000-8000-000000000202"
TUESDAY = "00000000-0000-4000-8000-000000000203"
RICE = "00000000-0000-4000-8000-000000000204"


async def get_items(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    """todo.get_items for one list."""
    resp = await hass.services.async_call(
        TODO_DOMAIN,
        "get_items",
        {},
        target={ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )
    return resp[entity_id]["items"]


def calls(aioclient_mock: AiohttpClientMocker) -> list[tuple[str, str, Any]]:
    """Requests made, as (method, path, body)."""
    return [(m.lower(), str(u).removeprefix(API), d) for m, u, d, _ in aioclient_mock.mock_calls]


# ── Entities ─────────────────────────────────────────────────────────────────


async def test_one_list_per_store(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Each store becomes a list on one "Two Spoons" service device."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, setup_integration.entry_id)
    assert sorted(e.unique_id for e in entries) == [
        "two_spoons_BK",
        "two_spoons_GG",
        "two_spoons_WW",
    ]

    state = hass.states.get(todo_entity_id(hass, "GG"))
    assert state.state == "1"  # one item still to buy
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Green Grocer"
    assert state.attributes["supported_features"] == 1 | 2 | 4  # create, delete, update; no move

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup_integration.entry_id)
    assert len(devices) == 1
    assert devices[0].name == "Two Spoons"
    assert devices[0].entry_type is dr.DeviceEntryType.SERVICE


async def test_items(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Items keep the API's order, summary, status and category."""
    items = await get_items(hass, todo_entity_id(hass, "GG"))
    assert items == [
        {
            "uid": APPLES,
            "summary": "Apples — 6",
            "status": "needs_action",
            "description": "Fruit & Veg",
        },
        {"uid": SPINACH, "summary": "Spinach", "status": "completed", "description": "Fruit & Veg"},
    ]


async def test_woolworths_alias(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """WW gets "Woolworths" as well as its own name; other stores get none."""
    registry = er.async_get(hass)
    assert registry.async_get(todo_entity_id(hass, "WW")).aliases == [
        er.COMPUTED_NAME,
        "Woolworths",
    ]
    assert registry.async_get(todo_entity_id(hass, "GG")).aliases == [er.COMPUTED_NAME]


async def test_alias_written_only_once(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A user who clears the alias doesn't get it back after a reload."""
    registry = er.async_get(hass)
    entity_id = todo_entity_id(hass, "WW")
    registry.async_update_entity(entity_id, aliases=[])
    mock_lists(aioclient_mock, lists_json)
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).aliases == []


async def test_new_store_added_on_refresh(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A store that appears later gets a list without a reload."""
    lists_json["stores"].append({"code": "DL", "name": "Deli", "items": []})
    mock_lists(aioclient_mock, lists_json)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(todo_entity_id(hass, "DL")).state == "0"


async def test_removed_store_unavailable_not_deleted(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A store that goes inactive stays registered, unavailable, also after a reload."""
    entity_id = todo_entity_id(hass, "BK")
    lists_json["stores"] = [s for s in lists_json["stores"] if s["code"] != "BK"]
    mock_lists(aioclient_mock, lists_json)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(entity_id) is not None
    state = hass.states.get(entity_id)
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Bakery"


async def test_store_renamed(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A store renamed in the app is renamed here; the entity id stays."""
    entity_id = todo_entity_id(hass, "BK")
    lists_json["stores"][2]["name"] = "Corner Bakery"
    mock_lists(aioclient_mock, lists_json)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes[ATTR_FRIENDLY_NAME] == "Two Spoons Corner Bakery"


async def test_poll_failure_makes_lists_unavailable(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failed poll marks lists unavailable until the next good one."""
    mock_lists(aioclient_mock, None, status=403, headers={"x-vercel-mitigated": "challenge"})
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(todo_entity_id(hass, "GG")).state == STATE_UNAVAILABLE


# ── Writes ───────────────────────────────────────────────────────────────────


async def test_add_item(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """add_item posts to this store, then refreshes."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", status=201, json={"merged": False})
    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {"item": "2 pears"},
        target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
        blocking=True,
    )
    assert calls(aioclient_mock) == [
        ("post", "/items", {"text": "2 pears", "store": "GG"}),
        ("get", "/lists", None),
    ]


async def test_add_item_merged(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A merge (200 + mergedInto) is a success too."""
    lists_json["stores"][0]["items"][0]["summary"] = "Apples — 8"
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", json={"merged": True, "mergedInto": APPLES})
    entity_id = todo_entity_id(hass, "GG")
    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {"item": "2 apples"},
        target={ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert (await get_items(hass, entity_id))[0]["summary"] == "Apples — 8"


async def test_tick_sends_summary_unchanged(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """Ticking sends the status plus the summary exactly as HA has it."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.patch(f"{API}/items/{APPLES}", json={"item": {}})
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"item": "Apples — 6", "status": "completed"},
        target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
        blocking=True,
    )
    assert calls(aioclient_mock) == [
        ("patch", f"/items/{APPLES}", {"status": "completed", "text": "Apples — 6"}),
        ("get", "/lists", None),
    ]


async def test_rename(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """Renaming keeps the current status."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.patch(f"{API}/items/{SPINACH}", json={"item": {}})
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"item": SPINACH, "rename": "Baby spinach — 2 bags"},
        target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
        blocking=True,
    )
    assert calls(aioclient_mock)[0] == (
        "patch",
        f"/items/{SPINACH}",
        {"status": "completed", "text": "Baby spinach — 2 bags"},
    )


async def test_rename_ready_meal_day(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """422 ready_meal_child_readonly becomes a clear validation error, then refreshes."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.patch(
        f"{API}/items/{MONDAY}", status=422, json={"error": "ready_meal_child_readonly"}
    )
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            TODO_DOMAIN,
            "update_item",
            {"item": MONDAY, "rename": "Ready Meals · Someday"},
            target={ATTR_ENTITY_ID: todo_entity_id(hass, "WW")},
            blocking=True,
        )
    assert exc.value.translation_key == "ready_meal_read_only"
    assert (
        str(exc.value)
        == "Ready Meals days can be ticked here, but only renamed or removed in Two Spoons"
    )
    assert calls(aioclient_mock)[-1] == ("get", "/lists", None)


async def test_update_missing_item(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """404 says the item is gone."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.patch(f"{API}/items/{APPLES}", status=404, json={"error": "not_found"})
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            TODO_DOMAIN,
            "update_item",
            {"item": APPLES, "status": "completed"},
            target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
            blocking=True,
        )
    assert exc.value.translation_key == "item_not_found"


async def test_write_401_starts_reauth(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected token on a write raises and asks for a new token."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/lists", status=401, json={"error": "unauthorized"})
    aioclient_mock.post(f"{API}/items", status=401, json={"error": "unauthorized"})
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            TODO_DOMAIN,
            "add_item",
            {"item": "Pears"},
            target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
            blocking=True,
        )
    assert exc.value.translation_key == "invalid_auth"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        (
            {"status": 403, "text": "x", "headers": {"x-vercel-mitigated": "challenge"}},
            "blocked_by_firewall",
        ),
        ({"status": 409, "json": {"error": "no_shopping_week"}}, "no_shopping_week"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 400, "json": {"error": "invalid_body"}}, "api_error"),
    ],
)
async def test_write_failures(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
    kwargs: dict[str, Any],
    key: str,
) -> None:
    """Other write failures surface as translated errors."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.post(f"{API}/items", **kwargs)
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            TODO_DOMAIN,
            "add_item",
            {"item": "Pears"},
            target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
            blocking=True,
        )
    assert exc.value.translation_key == key


async def test_delete_item(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """remove_item deletes by uid, then refreshes."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.delete(f"{API}/items/{APPLES}", json={"deleted": True, "id": APPLES})
    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {"item": ["Apples — 6"]},
        target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
        blocking=True,
    )
    assert calls(aioclient_mock) == [("delete", f"/items/{APPLES}", None), ("get", "/lists", None)]


async def test_remove_completed_skips_ready_meals(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """Bulk delete: the other items go, Ready Meals days are named in one error."""
    mock_lists(aioclient_mock, lists_json)
    for uid in (MONDAY, TUESDAY):
        aioclient_mock.delete(
            f"{API}/items/{uid}", status=422, json={"error": "ready_meal_child_readonly"}
        )
    aioclient_mock.delete(f"{API}/items/{RICE}", json={"deleted": True, "id": RICE})

    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            TODO_DOMAIN,
            "remove_completed_items",
            {},
            target={ATTR_ENTITY_ID: todo_entity_id(hass, "WW")},
            blocking=True,
        )
    assert exc.value.translation_key == "ready_meals_not_removed"
    assert str(exc.value) == (
        "2 Ready Meals day(s) weren't removed: "
        "Ready Meals · Monday 7th, Ready Meals · Tuesday 8th. "
        "Ready Meals days can only be removed in Two Spoons"
    )
    assert calls(aioclient_mock) == [
        ("delete", f"/items/{MONDAY}", None),
        ("delete", f"/items/{TUESDAY}", None),
        ("delete", f"/items/{RICE}", None),
        ("get", "/lists", None),
    ]


async def test_delete_already_gone_is_fine(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """Deleting an item someone else already removed isn't an error."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.delete(f"{API}/items/{SPINACH}", status=404, json={"error": "not_found"})
    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {"item": [SPINACH]},
        target={ATTR_ENTITY_ID: todo_entity_id(hass, "GG")},
        blocking=True,
    )


async def test_bulk_delete_stops_on_connection_error(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    lists_json: dict[str, Any],
) -> None:
    """A network failure stops the batch and is reported; the list refreshes."""
    mock_lists(aioclient_mock, lists_json)
    aioclient_mock.delete(f"{API}/items/{MONDAY}", exc=TimeoutError())
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            TODO_DOMAIN,
            "remove_completed_items",
            {},
            target={ATTR_ENTITY_ID: todo_entity_id(hass, "WW")},
            blocking=True,
        )
    assert exc.value.translation_key == "cannot_connect"
    assert calls(aioclient_mock) == [("delete", f"/items/{MONDAY}", None), ("get", "/lists", None)]
