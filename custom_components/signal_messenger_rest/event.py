"""Content-free event entity for incoming authorized messages."""

from homeassistant.components.event import EventEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CONF_RECEIVE
from .entity import SignalEntity


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.options.get(CONF_RECEIVE):
        async_add_entities([SignalMessageEvent(entry.runtime_data, "message")])


class SignalMessageEvent(SignalEntity, EventEntity):
    _attr_translation_key = "message"
    _attr_event_types = ["message_received", "reaction_received"]

    @property
    def available(self):
        return self.coordinator.connected

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.message_signal, self._message
            )
        )

    @callback
    def _message(self, event_type):
        self._trigger_event(event_type)
        self.async_write_ha_state()
