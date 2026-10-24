"""Week status and Woolies order sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Meals, Status
from .coordinator import TwoSpoonsConfigEntry, TwoSpoonsCoordinator
from .entity import TwoSpoonsEntity

PARALLEL_UPDATES = 0

WEEK_STATES = ["not_started", "partial", "planned"]
WOOLIES_STATES = ["not_placed", "exported", "placed", "pickup_today"]

# App status → sensor state ("ordered" means placed with Woolworths).
_WOOLIES_STATUS = {"not_started": "not_placed", "exported": "exported", "ordered": "placed"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwoSpoonsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add both sensors."""
    coordinator = entry.runtime_data
    async_add_entities([WeekStatusSensor(coordinator), WooliesOrderSensor(coordinator)])


def week_state(status: Status | None) -> str:
    """not_started (0 nights or no shopping week), partial (1-6), planned (all)."""
    if status is None or status.week.nights_planned <= 0:
        return "not_started"
    if status.week.nights_planned >= status.week.nights_total:
        return "planned"
    return "partial"


def woolies_state(status: Status) -> str:
    """not_placed / exported / placed, or pickup_today once placed on collection day."""
    state = _WOOLIES_STATUS.get(status.woolies.status, "not_placed")
    if state == "placed" and status.woolies.collection_date == status.today:
        return "pickup_today"
    return state


class _TwoSpoonsSensor(TwoSpoonsEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: TwoSpoonsCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"


class WeekStatusSensor(_TwoSpoonsSensor):
    """How much of the shopping week's dinners are planned."""

    _attr_options = WEEK_STATES

    def __init__(self, coordinator: TwoSpoonsCoordinator) -> None:
        """Week status for one config entry."""
        super().__init__(coordinator, "week_status")

    @property
    def native_value(self) -> str:
        """not_started / partial / planned."""
        return week_state(self.coordinator.data.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Counts for the shopping week, and tonight's dinner."""
        status: Status | None = self.coordinator.data.status
        meals: Meals | None = self.coordinator.data.meals
        if status is None:
            # No shopping week yet (409): nothing planned in it.
            return {
                "nights_planned": 0,
                "nights_total": 7,
                "shopping_week_start": meals.shopping_week_start if meals else None,
                "shopping_week_exists": False,
                "tonight": None,
            }
        return {
            "nights_planned": status.week.nights_planned,
            "nights_total": status.week.nights_total,
            "shopping_week_start": status.week.shopping_week_start,
            "shopping_week_exists": True,
            "tonight": status.week.tonight,
        }


class WooliesOrderSensor(_TwoSpoonsSensor):
    """The shopping week's Woolies click & collect order."""

    _attr_options = WOOLIES_STATES

    def __init__(self, coordinator: TwoSpoonsCoordinator) -> None:
        """Woolies order for one config entry."""
        super().__init__(coordinator, "woolies_order")

    @property
    def available(self) -> bool:
        """Unavailable with no shopping week (409)."""
        return super().available and self.coordinator.data.status is not None

    @property
    def native_value(self) -> str | None:
        """not_placed / exported / placed / pickup_today."""
        status = self.coordinator.data.status
        return None if status is None else woolies_state(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Dates and live item counts from the app."""
        status = self.coordinator.data.status
        if status is None:
            return None
        order = status.woolies
        return {
            "order_date": order.order_date,
            "collection_date": order.collection_date,
            "item_count": order.item_count,
            "items_remaining": order.items_remaining,
            "week_start": order.week_start,
        }
