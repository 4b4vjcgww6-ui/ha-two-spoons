"""One to-do list per Two Spoons store."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    StoreList,
    TwoSpoonsApiError,
    TwoSpoonsAuthError,
    TwoSpoonsError,
    TwoSpoonsFirewallError,
    TwoSpoonsNoShoppingWeekError,
)
from .const import DOMAIN, ERR_NOT_FOUND, ERR_READ_ONLY, STORE_ALIASES
from .coordinator import TwoSpoonsConfigEntry, TwoSpoonsCoordinator
from .entity import TwoSpoonsEntity

# Writes are sent one at a time, then the coordinator refreshes.
PARALLEL_UPDATES = 1

UNIQUE_ID_PREFIX = f"{DOMAIN}_"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwoSpoonsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a list per store, now and whenever a new store appears.

    Stores already in the entity registry are always added too, so a store
    that went inactive or was removed shows as unavailable instead of
    vanishing. Nothing is ever removed from the registry here.
    """
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    known: set[str] = set()

    registered: list[TwoSpoonsTodoList] = []
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != Platform.TODO or not reg_entry.unique_id.startswith(
            UNIQUE_ID_PREFIX
        ):
            continue
        code = reg_entry.unique_id.removeprefix(UNIQUE_ID_PREFIX)
        known.add(code)
        registered.append(
            TwoSpoonsTodoList(coordinator, code, reg_entry.original_name or code, is_new=False)
        )
    if registered:
        async_add_entities(registered)

    @callback
    def _add_new_stores() -> None:
        lists = coordinator.data.lists
        if lists is None:
            return
        new = [store for code, store in lists.stores.items() if code not in known]
        if not new:
            return
        known.update(store.code for store in new)
        async_add_entities(
            TwoSpoonsTodoList(coordinator, store.code, store.name, is_new=True) for store in new
        )

    _add_new_stores()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_stores))


class TwoSpoonsTodoList(TwoSpoonsEntity, TodoListEntity):
    """A store's shopping list for the shopping week."""

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self, coordinator: TwoSpoonsCoordinator, code: str, name: str, *, is_new: bool
    ) -> None:
        """Set up the list for one store code."""
        super().__init__(coordinator)
        self._code = code
        self._is_new = is_new
        self._attr_unique_id = f"{UNIQUE_ID_PREFIX}{code}"
        self._attr_name = name
        self._sync_from_data()

    @property
    def _store(self) -> StoreList | None:
        lists = self.coordinator.data.lists
        return None if lists is None else lists.stores.get(self._code)

    @property
    def available(self) -> bool:
        """Unavailable with no shopping week, or when the store isn't listed."""
        return super().available and self._store is not None

    def _sync_from_data(self) -> None:
        store = self._store
        if store is None:
            self._attr_todo_items = None
            return
        self._attr_name = store.name
        self._attr_todo_items = [
            TodoItem(
                uid=item.id,
                summary=item.summary,
                status=TodoItemStatus.COMPLETED if item.completed else TodoItemStatus.NEEDS_ACTION,
                description=item.description or None,
            )
            for item in store.items
        ]

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_from_data()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Give a newly created list its Assist alias, once."""
        await super().async_added_to_hass()
        alias = STORE_ALIASES.get(self._code)
        if not (self._is_new and alias):
            return
        registry = er.async_get(self.hass)
        reg_entry = registry.async_get(self.entity_id)
        # New entities start with no aliases or just their own name.
        if reg_entry is not None and reg_entry.aliases in ([], [er.COMPUTED_NAME]):
            # COMPUTED_NAME keeps the list's own name as an alias as well.
            registry.async_update_entity(self.entity_id, aliases=[er.COMPUTED_NAME, alias])

    # ── Writes ──────────────────────────────────────────────────────────────

    async def _write(self, call: Awaitable[None]) -> None:
        """Send one write, translate failures, and refresh either way."""
        try:
            await call
        except TwoSpoonsApiError as err:
            if err.code == ERR_READ_ONLY:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="ready_meal_read_only"
                ) from err
            if err.code == ERR_NOT_FOUND:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="item_not_found"
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"code": err.code},
            ) from err
        except TwoSpoonsError as err:
            raise self._translate(err) from err
        finally:
            await self.coordinator.async_refresh()

    def _translate(self, err: TwoSpoonsError) -> HomeAssistantError:
        if isinstance(err, TwoSpoonsAuthError):
            self.coordinator.config_entry.async_start_reauth(self.hass)
            key = "invalid_auth"
        elif isinstance(err, TwoSpoonsFirewallError):
            key = "blocked_by_firewall"
        elif isinstance(err, TwoSpoonsNoShoppingWeekError):
            key = "no_shopping_week"
        else:
            key = "cannot_connect"
        return HomeAssistantError(translation_domain=DOMAIN, translation_key=key)

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add to this store via the API's Quick Add pipeline."""
        await self._write(self.coordinator.client.async_create_item(self._code, item.summary or ""))

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Tick, untick or rename. The summary is sent exactly as HA has it."""
        assert item.uid is not None
        await self._write(
            self.coordinator.client.async_update_item(
                item.uid,
                completed=item.status == TodoItemStatus.COMPLETED,
                text=item.summary or "",
            )
        )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete each item, skipping Ready Meals days and reporting them once.

        HA's "remove completed items" sends every ticked item, which can
        include Ready Meals days; those can only be removed in the app. An
        item that's already gone counts as deleted.
        """
        summaries = {item.uid: item.summary for item in self.todo_items or ()}
        read_only: list[str] = []
        try:
            for uid in uids:
                try:
                    await self.coordinator.client.async_delete_item(uid)
                except TwoSpoonsApiError as err:
                    if err.code == ERR_READ_ONLY:
                        read_only.append(summaries.get(uid) or uid)
                    elif err.code != ERR_NOT_FOUND:
                        raise HomeAssistantError(
                            translation_domain=DOMAIN,
                            translation_key="api_error",
                            translation_placeholders={"code": err.code},
                        ) from err
                except TwoSpoonsError as err:
                    raise self._translate(err) from err
        finally:
            await self.coordinator.async_refresh()
        if read_only:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="ready_meal_day_not_removed"
                if len(read_only) == 1
                else "ready_meal_days_not_removed",
                translation_placeholders={
                    "count": str(len(read_only)),
                    "items": ", ".join(read_only),
                },
            )
