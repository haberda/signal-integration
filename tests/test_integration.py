"""Real HA entity setup, actions, events, reload and diagnostics."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_messenger_rest.api import CannotConnect, InvalidAuth
from custom_components.signal_messenger_rest.const import DOMAIN, EVENT_MESSAGE
from custom_components.signal_messenger_rest.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_config_flow import CONNECTION, SETTINGS
from .test_models import RAW


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Signal test",
        data={**CONNECTION, "account": "+12025550100"},
        options={
            **SETTINGS,
            "destinations": [
                {"id": "alice", "recipient": "+12025550101", "name": "Alice"},
                {"id": "group", "recipient": "group.test", "name": "Household"},
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_entities_sending_and_unload(hass, entry, api_mock):
    with patch(
        "custom_components.signal_messenger_rest.api.SignalClient.send",
        new_callable=AsyncMock,
    ) as send:
        send.return_value = {"timestamp": "123"}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "notify", DOMAIN, f"{entry.entry_id}_alice"
        )
        assert entity_id
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": entity_id, "message": "hello", "title": "Alert"},
            blocking=True,
        )
        assert send.call_args.args[2] == "Alert\n\nhello"
        assert hass.states.get(entity_id).state not in {"unknown", "unavailable"}
        response = await hass.services.async_call(
            DOMAIN,
            "send_message",
            {
                "message": "reply",
                "quote_timestamp": 1000,
                "quote_author": "sender-uuid",
            },
            blocking=True,
            return_response=True,
        )
        assert response["success"]
        assert len(response["results"]) == 2
        assert send.call_args.kwargs["quote_timestamp"] == 1000
        assert all(len(call.args[1]) == 1 for call in send.call_args_list)
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state == ConfigEntryState.NOT_LOADED
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "send_message", {"message": "hello"}, blocking=True
            )


async def test_partial_failure_and_ambiguous_accounts(hass, entry, api_mock):
    assert await hass.config_entries.async_setup(entry.entry_id)
    with patch(
        "custom_components.signal_messenger_rest.api.SignalClient.send",
        new_callable=AsyncMock,
    ) as send:
        send.side_effect = [{"timestamp": "1000"}, CannotConnect("Delivery uncertain")]
        response = await hass.services.async_call(
            DOMAIN,
            "send_message",
            {"message": "test"},
            blocking=True,
            return_response=True,
        )
        assert response == {
            "success": False,
            "results": [
                {"recipient": "+12025550101", "success": True, "timestamp": "1000"},
                {
                    "recipient": "group.test",
                    "success": False,
                    "error": "Delivery uncertain",
                },
            ],
        }
        send.side_effect = CannotConnect("Delivery uncertain")
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "send_message", {"message": "test"}, blocking=True
            )
        second = MockConfigEntry(domain=DOMAIN, data=entry.data, options=entry.options)
        second.add_to_hass(hass)
        assert await hass.config_entries.async_setup(second.entry_id)
        with pytest.raises(ServiceValidationError, match="Select one"):
            await hass.services.async_call(
                DOMAIN, "send_message", {"message": "test"}, blocking=True
            )
        await hass.config_entries.async_unload(second.entry_id)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_receiver_filters_content_and_stops(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, "receive": True, "allowed_senders": ["sender-uuid"]},
    )
    waiting = asyncio.Event()
    closed = asyncio.Event()

    async def messages(_self, _account):
        try:
            yield {"_connected": True}
            yield RAW
            yield RAW  # Duplicate delivery.
            blocked = deepcopy(RAW)
            blocked["envelope"]["sourceUuid"] = "intruder"
            blocked["envelope"]["dataMessage"]["message"] = "private unauthorized text"
            yield blocked
            waiting.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    with patch(
        "custom_components.signal_messenger_rest.api.SignalClient.messages", messages
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await waiting.wait()
        await hass.async_block_till_done()
        assert len(events) == 1
        assert events[0].data["text"] == "status"
        assert events[0].data["config_entry_id"] == entry.entry_id
        assert entry.runtime_data.connected
        states = str(hass.states.async_all())
        assert "private unauthorized text" not in states
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert "sender-uuid" not in str(diagnostics)
        assert "+12025550100" not in str(diagnostics)
        assert CONNECTION["url"] not in str(diagnostics)
        runtime = entry.runtime_data
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert closed.is_set()
        assert not runtime.connected


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (InvalidAuth(), ConfigEntryState.SETUP_ERROR),
        (CannotConnect(), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failures(hass, entry, api_mock, error, state):
    api_mock[0].side_effect = error
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == state
    await hass.async_block_till_done()


async def test_options_reload_removes_destination(hass, entry, api_mock):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    alice = registry.async_get_entity_id("notify", DOMAIN, f"{entry.entry_id}_alice")
    group = registry.async_get_entity_id("notify", DOMAIN, f"{entry.entry_id}_group")
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, "destinations": [entry.options["destinations"][0]]},
    )
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED
    assert registry.async_get(alice)
    assert registry.async_get(group) is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_health_and_mode_change(hass, entry, api_mock):
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    with patch.object(runtime.client, "request", new_callable=AsyncMock) as request:
        request.side_effect = CannotConnect()
        await runtime.async_refresh()
        assert not runtime.last_update_success
        request.side_effect = None
        request.return_value = {"mode": "json-rpc", "version": "0.100"}
        await runtime.async_refresh()
        assert runtime.last_update_success
        with patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ) as reload:
            request.return_value = {"mode": "native"}
            await runtime.async_refresh()
            await hass.async_block_till_done()
            reload.assert_awaited_once_with(entry.entry_id)
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "fields",
    [
        {"message": "", "recipients": []},
        {"message": "reply", "quote_timestamp": 123},
        {"message": "reply", "quote_message": "quote"},
        {"message": ""},
        {"message": "x" * 10001},
    ],
)
async def test_bad_sends_do_not_touch_backend(hass, entry, api_mock, fields):
    assert await hass.config_entries.async_setup(entry.entry_id)
    with patch.object(
        entry.runtime_data.client, "send", new_callable=AsyncMock
    ) as send:
        with pytest.raises((ServiceValidationError, vol.Invalid)):
            await hass.services.async_call(
                DOMAIN, "send_message", fields, blocking=True
            )
        send.assert_not_awaited()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_rich_send_local_attachment_and_self(hass, entry, api_mock, tmp_path):
    file = tmp_path / "image.jpg"
    file.write_bytes(b"photo")
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await hass.config_entries.async_setup(entry.entry_id)
    with patch.object(
        entry.runtime_data.client,
        "send",
        new_callable=AsyncMock,
        return_value={"timestamp": "123"},
    ) as send:
        response = await hass.services.async_call(
            DOMAIN,
            "send_message",
            {
                "config_entry_id": entry.entry_id,
                "recipients": ["+12025550100"],
                "message": "image",
                "attachments": [str(file)],
                "text_mode": "styled",
                "quote_timestamp": 1000,
                "quote_author": "sender-uuid",
                "quote_message": "Original",
            },
            blocking=True,
            return_response=True,
        )
        assert response["success"]
        assert send.call_args.kwargs["base64_attachments"] == [
            "data:image/jpeg;filename=image.jpg;base64,cGhvdG8="
        ]
        assert send.call_args.kwargs["text_mode"] == "styled"
        assert send.call_args.kwargs["quote_message"] == "Original"
    await hass.config_entries.async_unload(entry.entry_id)
