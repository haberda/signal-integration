"""Acknowledgment matching, fast replies, expiry and lifecycle cancellation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.signal_messenger_rest.alerts import matches_ack, send_alert
from custom_components.signal_messenger_rest.const import DOMAIN, EVENT_REACTION


def ack(entry):
    return {
        "config_entry_id": entry.entry_id,
        "account": entry.data["account"],
        "target_timestamp": 1000,
        "target_author_number": entry.data["account"],
        "target_author_uuid": "account-uuid",
        "target_author": "account-uuid",
        "conversation_id": "+12025550101",
        "conversation_kind": "direct",
        "sender_uuid": "sender-uuid",
        "sender_number": "+12025550101",
        "emoji": "✅",
        "removed": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"removed": True},
        {"emoji": "👍"},
        {"target_timestamp": 999},
        {"config_entry_id": "other"},
        {"account": "other"},
        {"conversation_kind": "group"},
        {
            "target_author_number": None,
            "target_author_uuid": "other",
            "target_author": "other",
        },
    ],
)
def test_only_original_alert_matches(entry, change):
    values = dict(
        entry_id=entry.entry_id,
        account=entry.data["account"],
        account_author="",
        recipient="+12025550101",
        timestamp=1000,
        emoji="✅",
    )
    assert matches_ack(ack(entry), **values)
    assert not matches_ack({**ack(entry), **change}, **values)


def test_uuid_only_and_group(entry):
    data = {
        **ack(entry),
        "target_author_number": None,
        "conversation_kind": "group",
        "conversation_id": "group.test",
    }
    values = dict(
        entry_id=entry.entry_id,
        account=entry.data["account"],
        account_author="account-uuid",
        recipient="group.test",
        timestamp=1000,
        emoji="✅",
    )
    assert matches_ack(data, **values)
    assert not matches_ack({**data, "conversation_id": "group.other"}, **values)
    assert not matches_ack(data, **{**values, "account_author": ""})


async def runtime_for(hass, entry):
    # Avoid starting a real receiver while testing the alert action.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "receive": True}
    )
    with patch(
        "custom_components.signal_messenger_rest.receiver.Receiver.run",
        new_callable=AsyncMock,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data


async def test_fast_ack_before_send_returns(hass, entry, api_mock):
    runtime = await runtime_for(hass, entry)

    async def send(*args, **kwargs):
        hass.bus.async_fire(EVENT_REACTION, ack(entry))
        await asyncio.sleep(0)
        return {"success": True, "results": [{"timestamp": "1000"}]}

    with patch.object(runtime, "send_message", side_effect=send) as send_mock:
        result = await send_alert(
            runtime, recipient="+12025550101", message="alert", expiry=1
        )
    assert result["acknowledged"]
    assert send_mock.call_count == 1
    assert not runtime.alert_tasks
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reminders_keep_original_reference(hass, entry, api_mock):
    runtime = await runtime_for(hass, entry)
    calls = []

    async def send(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            hass.bus.async_fire(EVENT_REACTION, ack(entry))
            await asyncio.sleep(0)
        return {"success": True, "results": [{"timestamp": str(999 + len(calls))}]}

    with patch.object(runtime, "send_message", side_effect=send):
        result = await send_alert(
            runtime,
            recipient="+12025550101",
            message="alert",
            expiry=1,
            reminder_interval=0.01,
        )
    assert result["timestamp"] == "1000" and result["acknowledged"]
    assert calls[1]["quote_timestamp"] == 1000
    await hass.config_entries.async_unload(entry.entry_id)


async def test_expiry_and_failed_initial_send(hass, entry, api_mock):
    runtime = await runtime_for(hass, entry)
    with patch.object(runtime, "send_message", new_callable=AsyncMock) as send:
        send.return_value = {"success": True, "results": [{"timestamp": "1000"}]}
        result = await send_alert(
            runtime, recipient="+12025550101", message="alert", expiry=0.01
        )
        assert result["reason"] == "expired"
        assert send.await_count == 1
        send.return_value = {"success": False}
        with pytest.raises(ServiceValidationError, match="no reminders"):
            await send_alert(runtime, recipient="+12025550101", message="alert")
    assert not runtime.alert_tasks
    assert EVENT_REACTION not in hass.bus.async_listeners()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_wait_and_listener(hass, entry, api_mock):
    runtime = await runtime_for(hass, entry)
    sent = asyncio.Event()

    async def send(*args, **kwargs):
        sent.set()
        return {"success": True, "results": [{"timestamp": "1000"}]}

    with patch.object(runtime, "send_message", side_effect=send):
        task = asyncio.create_task(
            hass.services.async_call(
                DOMAIN,
                "send_alert",
                {"recipient": "+12025550101", "message": "alert"},
                blocking=True,
                return_response=True,
            )
        )
        await sent.wait()
        await hass.config_entries.async_unload(entry.entry_id)
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not runtime.alert_tasks
    assert EVENT_REACTION not in hass.bus.async_listeners()
