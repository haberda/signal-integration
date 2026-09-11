"""Diagnostic timestamps without message content."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_RECEIVE
from .entity import SignalEntity


async def async_setup_entry(hass, entry, async_add_entities):
    keys = ["last_sent"]
    if entry.options.get(CONF_RECEIVE):
        keys.append("last_received")
    async_add_entities(SignalTimestamp(entry.runtime_data, key) for key in keys)


class SignalTimestamp(SignalEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key
        self._attr_translation_key = key

    @property
    def native_value(self):
        return getattr(self.coordinator, self.key)
