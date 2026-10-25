"""The "Meals" calendar: planned dinners and weekend baking, read-only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import MealNight, Meals
from .coordinator import TwoSpoonsConfigEntry, TwoSpoonsCoordinator
from .entity import TwoSpoonsEntity

PARALLEL_UPDATES = 0

APP_TIMEZONE = "Australia/Perth"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwoSpoonsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Meals calendar."""
    coordinator = entry.runtime_data
    meals = coordinator.data.meals
    # "Tonight" follows the app's timezone (Perth), not Home Assistant's.
    tz = await dt_util.async_get_time_zone(meals.timezone if meals else APP_TIMEZONE)
    async_add_entities([TwoSpoonsMealsCalendar(coordinator, tz or dt_util.get_default_time_zone())])


def _description(night: MealNight) -> str | None:
    """Fixed lines, each only when set: kind, method, temp, cook, note."""
    lines = [
        f"{key}: {value}"
        for key, value in (
            ("kind", night.kind),
            ("method", night.cook_method),
            ("temp", None if night.oven_temp_c is None else f"{night.oven_temp_c}°C"),
            ("cook", None if night.cook_mins is None else f"{night.cook_mins} min"),
            ("note", night.note),
        )
        if value
    ]
    return "\n".join(lines) or None


def meal_events(meals: Meals, today: str) -> tuple[list[CalendarEvent], CalendarEvent | None]:
    """All events in the window, plus tonight's dinner (None if unplanned).

    One all-day event per planned night; baking spans that week's Saturday
    and Sunday. `today` is an ISO date in the app's timezone.
    """
    events: list[CalendarEvent] = []
    tonight: CalendarEvent | None = None
    for night in meals.nights:
        if not night.planned or not night.label:
            continue
        start = date.fromisoformat(night.date)
        event = CalendarEvent(
            start=start,
            end=start + timedelta(days=1),
            summary=night.label,
            description=_description(night),
            uid=f"dinner-{night.date}",
        )
        events.append(event)
        if night.date == today:
            tonight = event
    for baking in meals.baking:
        start = date.fromisoformat(baking.week_start)
        events.append(
            CalendarEvent(
                start=start,
                end=start + timedelta(days=2),
                summary=f"Baking: {baking.label}",
                uid=f"baking-{baking.week_start}",
            )
        )
    events.sort(key=lambda e: (e.start, e.end))  # a night before the baking weekend
    return events, tonight


class TwoSpoonsMealsCalendar(TwoSpoonsEntity, CalendarEntity):
    """Dinners for this week and next. The state is tonight's dinner."""

    _attr_translation_key = "meals"

    def __init__(self, coordinator: TwoSpoonsCoordinator, tz: tzinfo) -> None:
        """One calendar per config entry."""
        super().__init__(coordinator)
        self._tz = tz
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_meals"

    @property
    def available(self) -> bool:
        """Unavailable until /meals has been read."""
        return super().available and self.coordinator.data.meals is not None

    def _events(self) -> tuple[list[CalendarEvent], CalendarEvent | None]:
        meals = self.coordinator.data.meals
        if meals is None:
            return [], None
        return meal_events(meals, dt_util.now(self._tz).date().isoformat())

    @property
    def event(self) -> CalendarEvent | None:
        """Tonight's dinner, or None when tonight isn't planned."""
        return self._events()[1]

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping the requested range."""
        return [
            event
            for event in self._events()[0]
            if event.start_datetime_local < end_date and event.end_datetime_local > start_date
        ]
