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

from .test_config_flow import CONNECTION
from .test_models import RAW


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
            "partial": False,
            "results": [
                {
                    "recipient": "+12025550101",
                    "success": True,
                    "status": "sent",
                    "timestamp": "1000",
                },
                {
                    "recipient": "group.test",
                    "success": False,
                    "status": "failed",
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


async def test_destination_name_drives_entity_name_and_initial_id(
    hass, entry, api_mock
):
    destination = {**entry.options["destinations"][0], "name": "Family alerts"}
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "destinations": [destination]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "notify", DOMAIN, f"{entry.entry_id}_alice"
    )
    assert entity_id == "notify.signal_test_family_alerts"
    assert (
        hass.states.get(entity_id).attributes["friendly_name"]
        == "Signal test Family alerts"
    )
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "destinations": [{**destination, "name": "Home alerts"}],
        },
    )
    await hass.async_block_till_done()
    assert (
        registry.async_get_entity_id("notify", DOMAIN, f"{entry.entry_id}_alice")
        == entity_id
    )
    assert (
        hass.states.get(entity_id).attributes["friendly_name"]
        == "Signal test Home alerts"
    )
    await hass.config_entries.async_unload(entry.entry_id)


async def test_send_time_limit(hass, entry, api_mock):
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    module = "custom_components.signal_messenger_rest.coordinator"

    async def stall(*_args, **_kwargs):
        await asyncio.Event().wait()

    with (
        patch(f"{module}.SEND_DEADLINE", 0.05),
        patch(
            "custom_components.signal_messenger_rest.api.SignalClient.send",
            side_effect=stall,
        ) as send,
    ):
        result = await runtime.send_message(["+12025550101", "group.test"], "test")
        # The stalled send is uncertain; the next destination is never attempted.
        assert send.call_count == 1
        assert not result["success"]
        assert "may already have succeeded" in result["results"][0]["error"]
        assert result["results"][1]["error"].startswith("Not attempted")
        assert not runtime.send_lock.locked()

        async with runtime.send_lock:
            with pytest.raises(ServiceValidationError, match="nothing was sent"):
                await runtime.send_message(["+12025550101"], "test")
        assert send.call_count == 1

        with (
            patch(f"{module}.encode_attachments", side_effect=stall),
            pytest.raises(ServiceValidationError, match="nothing was sent"),
        ):
            await runtime.send_message(["+12025550101"], "test")
        assert send.call_count == 1
        assert not runtime.send_lock.locked()
    await hass.config_entries.async_unload(entry.entry_id)


MEMBER_UUID = "0f6f2a0c-5d0e-4c57-9a5e-3f2b7c1d9e10"
# Shape returned by signal-cli-rest-api 0.101+ when signal-cli reports a
# non-success result for a recipient: HTTP 201, timestamp and errors.recipients.
MEMBER_FAILED = {
    "timestamp": "1000",
    "errors": {
        "recipients": [
            {
                "number": "+12025550102",
                "uuid": MEMBER_UUID,
                "reason": "IDENTITY_FAILURE",
            },
            {"uuid": MEMBER_UUID, "reason": "A_FUTURE_TYPE private detail"},
        ]
    },
}


async def test_group_member_failure_is_a_partial_send(hass, entry, api_mock, caplog):
    from homeassistant.helpers import issue_registry as ir

    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    issues = ir.async_get(hass)
    with patch(
        "custom_components.signal_messenger_rest.api.SignalClient.send",
        new_callable=AsyncMock,
    ) as send:
        # Direct and group sends without failures.
        send.return_value = {"timestamp": "999"}
        clean = await runtime.send_message(["+12025550101", "group.test"], "test")
        assert clean["success"] and not clean["partial"]
        assert [r["status"] for r in clean["results"]] == ["sent", "sent"]
        assert runtime.last_send_outcome["status"] == "sent"

        runtime.last_sent = None
        send.return_value = MEMBER_FAILED
        # Without response data, a partial group send must not raise.
        await hass.services.async_call(
            DOMAIN,
            "send_message",
            {"recipients": ["group.test"], "message": "test"},
            blocking=True,
        )
        response = await hass.services.async_call(
            DOMAIN,
            "send_message",
            {"recipients": ["group.test"], "message": "test"},
            blocking=True,
            return_response=True,
        )
        assert response == {
            "success": True,
            "partial": True,
            "results": [
                {
                    "recipient": "group.test",
                    "success": True,
                    "status": "partial",
                    "timestamp": "1000",
                    "failed_members": [
                        {"member": "…02", "reason": "identity_failure"},
                        {"member": "0f6f2a0c…", "reason": "unknown_failure"},
                    ],
                }
            ],
        }
        assert runtime.last_sent is not None
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": "notify.signal_test_household", "message": "test"},
            blocking=True,
        )

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["last_send"] == {
            "status": "partial",
            "destinations": 1,
            "failed_destinations": 0,
            "failed_members": {"identity_failure": 1, "unknown_failure": 1},
        }
        issue = issues.async_get_issue(DOMAIN, runtime.identity_issue_id)
        assert issue.translation_placeholders["members"] == "…02"
        for private in ("+12025550102", MEMBER_UUID, "group.test", "private detail"):
            assert private not in caplog.text
            assert private not in str(diagnostics)
            assert private not in str(issue.translation_placeholders)
        assert "identity_failure: 1" in caplog.text

        # The same report for a direct recipient means it was not delivered.
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                "send_message",
                {"recipients": ["+12025550102"], "message": "test"},
                blocking=True,
            )
        direct = await runtime.send_message(["+12025550102"], "test")
        assert not direct["success"] and not direct["partial"]
        assert direct["results"][0]["status"] == "failed"
        assert "identity_failure" in direct["results"][0]["error"]

        # An unrecognized failure report stays a failure.
        send.return_value = {"timestamp": "1000", "errors": {"unexpected": True}}
        unknown = await runtime.send_message(["group.test"], "test")
        assert unknown["results"][0]["status"] == "failed"
        assert issues.async_get_issue(DOMAIN, runtime.identity_issue_id)

        # Fully delivered sends to both affected destinations clear the repair.
        send.return_value = {"timestamp": "1001"}
        await runtime.send_message(["group.test"], "test")
        assert issues.async_get_issue(DOMAIN, runtime.identity_issue_id)
        await runtime.send_message(["+12025550102"], "test")
        assert issues.async_get_issue(DOMAIN, runtime.identity_issue_id) is None

        send.return_value = MEMBER_FAILED
        await runtime.send_message(["group.test"], "test")
        assert issues.async_get_issue(DOMAIN, runtime.identity_issue_id)
    await hass.config_entries.async_unload(entry.entry_id)
    assert issues.async_get_issue(DOMAIN, runtime.identity_issue_id) is None


async def test_group_alert_uses_partial_send_reference(hass, entry, api_mock):
    from custom_components.signal_messenger_rest.alerts import send_alert

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "receive": True}
    )

    async def messages(_self, _account):
        yield {"_connected": True}
        await asyncio.Event().wait()

    with (
        patch(
            "custom_components.signal_messenger_rest.api.SignalClient.messages",
            messages,
        ),
        patch(
            "custom_components.signal_messenger_rest.api.SignalClient.send",
            new_callable=AsyncMock,
            return_value=MEMBER_FAILED,
        ) as send,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        result = await send_alert(
            entry.runtime_data,
            recipient="group.test",
            message="Alert",
            expiry=1,
            reminder_interval=0.2,
        )
        assert result == {
            "acknowledged": False,
            "reason": "expired",
            "timestamp": "1000",
        }
        # Reminders followed and quoted the original, partially delivered alert.
        assert send.await_count > 1
        assert send.await_args.kwargs["quote_timestamp"] == 1000
        await hass.config_entries.async_unload(entry.entry_id)
