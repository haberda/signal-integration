"""Shared account entity metadata."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SignalCoordinator


class SignalEntity(CoordinatorEntity[SignalCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SignalCoordinator, key: str):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="Signal CLI REST API",
            model="Signal account",
            sw_version=coordinator.data.get("version"),
        )
