"""Rich send action with optional structured response data."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import CONF_DESTINATIONS, DOMAIN

SEND_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("recipients"): vol.All(
            cv.ensure_list, [cv.string], vol.Length(min=1, max=20)
        ),
        vol.Optional("message", default=""): cv.string,
        vol.Optional("title"): cv.string,
        vol.Optional("attachments", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("urls", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("text_mode", default="normal"): vol.In(["normal", "styled"]),
        vol.Optional("quote_timestamp"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("quote_author"): cv.string,
        vol.Optional("quote_message"): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "send_message"):
        return

    async def send(call: ServiceCall):
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state == ConfigEntryState.LOADED
        ]
        requested = call.data.get("config_entry_id")
        if requested:
            entries = [e for e in entries if e.entry_id == requested]
        if len(entries) != 1:
            raise ServiceValidationError(
                "Select one loaded Signal account using config_entry_id"
            )
        entry = entries[0]
        fields = dict(call.data)
        fields.pop("config_entry_id", None)
        recipients = fields.pop(
            "recipients",
            [d["recipient"] for d in entry.options.get(CONF_DESTINATIONS, [])],
        )
        result = await entry.runtime_data.send_message(recipients, **fields)
        if not result["success"] and not call.return_response:
            raise ServiceValidationError(
                "One or more Signal sends failed; use response data for per-destination results. Do not blindly retry."
            )
        return result

    hass.services.async_register(
        DOMAIN,
        "send_message",
        send,
        schema=SEND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
