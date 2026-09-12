"""Discover running Signal add-ons through Home Assistant's Supervisor data."""

from homeassistant.components.hassio import get_addons_info
from homeassistant.components.hassio.exceptions import HassioNotReadyError

ADDONS = {
    "1315902c_signal_messenger": "Signal Messenger (production)",
    "c5ecc243_signal_messenger": "Signal Messenger (edge)",
}


def running_addons(hass) -> dict[str, str]:
    """Use internal DNS and the container port, regardless of published ports."""
    try:
        installed = get_addons_info(hass)
    except HassioNotReadyError:
        return {}
    return {
        f"http://{info.get('hostname') or slug.replace('_', '-')}:8080": label
        for slug, label in ADDONS.items()
        if (info := installed.get(slug)) and info.get("state") == "started"
    }
