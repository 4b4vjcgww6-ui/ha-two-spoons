"""Base entity: everything sits on one "Two Spoons" service device."""

from __future__ import annotations

from homeassistant.const import CONF_URL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TwoSpoonsCoordinator


class TwoSpoonsEntity(CoordinatorEntity[TwoSpoonsCoordinator]):
    """A Two Spoons entity on the shared device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TwoSpoonsCoordinator) -> None:
        """Attach to the config entry's device."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Two Spoons",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_URL],
        )
