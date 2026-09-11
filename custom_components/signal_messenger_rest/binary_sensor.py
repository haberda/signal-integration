"""Separate API reachability from the receiving transport."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_RECEIVE
from .entity import SignalEntity


async def async_setup_entry(hass, entry, async_add_entities):
    keys = ["api_connected"]
    if entry.options.get(CONF_RECEIVE):
        keys.append("receiver_connected")
    async_add_entities(SignalConnection(entry.runtime_data, key) for key in keys)


class SignalConnection(SignalEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_available = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key
        self._attr_translation_key = key

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return (
            self.coordinator.last_update_success
            if self.key == "api_connected"
            else self.coordinator.connected
        )
