"""Content-free diagnostic timestamps and Assist health."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_RECEIVE
from .entity import SignalEntity


async def async_setup_entry(hass, entry, async_add_entities):
    keys = ["last_sent"]
    if entry.options.get(CONF_RECEIVE):
        keys.append("last_received")
    async_add_entities(
        [SignalTimestamp(entry.runtime_data, key) for key in keys]
        + [SignalAssistSensor(entry.runtime_data, key) for key in ASSIST_VALUES]
    )


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


ASSIST_VALUES = {
    "assist_status": lambda assist: assist.status,
    "assist_queued": lambda assist: assist.queue.qsize(),
    "assist_completed": lambda assist: assist.completed,
    "assist_errors": lambda assist: assist.failed,
    "assist_rejected": lambda assist: assist.dropped,
    "assist_feedback_suppressed": lambda assist: assist.feedback_suppressed,
    "assist_last_error": lambda assist: assist.last_error,
}
ASSIST_ENUMS = {
    "assist_status": [
        "disabled",
        "receiving_disabled",
        "disconnected",
        "pipeline_unavailable",
        "processing",
        "idle",
        "stopped",
    ],
    "assist_last_error": ["none", "pipeline_failed", "reply_failed", "feedback_failed"],
}


class SignalAssistSensor(SignalEntity, SensorEntity):
    """Runtime counters and status remain readable when the API is down."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_available = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key
        self._attr_translation_key = key
        if key in ASSIST_ENUMS:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ASSIST_ENUMS[key]

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return ASSIST_VALUES[self.key](self.coordinator.assist)
