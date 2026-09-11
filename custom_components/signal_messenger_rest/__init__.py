"""UI-configured Signal notifications and incoming events."""

import asyncio
from contextlib import suppress

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import entity_registry as er

from .api import InvalidAuth, SignalError
from .config_flow import client_for
from .const import (
    CONF_ACCOUNT,
    CONF_DESTINATIONS,
    CONF_INTERVAL,
    CONF_RECEIVE,
    DEFAULT_INTERVAL,
    DOMAIN,
)
from .coordinator import SignalCoordinator
from .receiver import Receiver
from .services import async_setup_services

PLATFORMS = [Platform.NOTIFY, Platform.EVENT, Platform.BINARY_SENSOR, Platform.SENSOR]
type SignalConfigEntry = ConfigEntry[SignalCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SignalConfigEntry) -> bool:
    client = client_for(hass, entry.data)
    try:
        about, accounts = await client.discover()
    except InvalidAuth:
        raise ConfigEntryAuthFailed("API authentication failed") from None
    except SignalError:
        raise ConfigEntryNotReady("Cannot connect to Signal API") from None
    if entry.data[CONF_ACCOUNT] not in accounts:
        raise ConfigEntryError(
            "Signal account is no longer linked; link it in the backend again"
        )
    coordinator = entry.runtime_data = SignalCoordinator(hass, entry, client, about)
    # Remove only destinations explicitly removed in options; leave other user entities alone.
    registry = er.async_get(hass)
    valid = {
        f"{entry.entry_id}_{d['id']}" for d in entry.options.get(CONF_DESTINATIONS, [])
    }
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == "notify" and entity.unique_id not in valid:
            registry.async_remove(entity.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    if entry.options.get(CONF_RECEIVE, False):
        receiver = coordinator.receiver = Receiver(
            client,
            coordinator.account,
            entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL),
            coordinator.receive_message,
            coordinator.receive_status,
            coordinator.auth_failed,
        )
        coordinator.receiver_task = entry.async_create_background_task(
            hass, receiver.run(), f"{DOMAIN} receiver"
        )

    async def stop_receiver(_event=None):
        await coordinator.stop_alerts()
        if coordinator.receiver_task is not None:
            coordinator.receiver_task.cancel()
            with suppress(asyncio.CancelledError):
                await coordinator.receiver_task
            coordinator.receiver_task = None

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_receiver)
    )
    return True


async def async_reload_entry(hass: HomeAssistant, entry: SignalConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SignalConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop_alerts()
        task = entry.runtime_data.receiver_task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Services remain registered and resolve only currently loaded entries.
    return unloaded
