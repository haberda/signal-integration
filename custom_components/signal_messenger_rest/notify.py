"""Modern notification entities for selected destinations."""

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_DESTINATIONS
from .entity import SignalEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        SignalNotify(entry.runtime_data, destination)
        for destination in entry.options.get(CONF_DESTINATIONS, [])
    )


class SignalNotify(SignalEntity, NotifyEntity):
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, coordinator, destination):
        super().__init__(coordinator, destination["id"])
        self.recipient = destination["recipient"]
        self._attr_name = destination["name"]

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        result = await self.coordinator.send_message(
            [self.recipient], message, title=title
        )
        if not result["success"]:
            raise HomeAssistantError(result["results"][0]["error"])
