"""Read receipts respect routing, bounds and integration lifecycle."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.signal_messenger_rest.api import InvalidAuth, SignalError
from custom_components.signal_messenger_rest.models import Reaction
from custom_components.signal_messenger_rest.receipts import MAX_PENDING

from .test_assist import MESSAGE


@pytest.fixture
async def runtime(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "receive": True,
            "send_read_receipts": True,
            "allowed_senders": ["alice"],
            "allowed_groups": ["group.test"],
        },
    )
    with patch(
        "custom_components.signal_messenger_rest.receiver.Receiver.run",
        new_callable=AsyncMock,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        runtime = entry.runtime_data
        runtime.client.send_read_receipt = AsyncMock()
        yield runtime
        await hass.config_entries.async_unload(entry.entry_id)


async def drain(runtime):
    await runtime.receipts.queue.join()
    if runtime.receipts.task:
        await runtime.receipts.task


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("number", ["+12025550101", None])
async def test_accepted_messages_receipted_once_to_sender(runtime, group, number):
    message = replace(
        MESSAGE,
        sender_number=number,
        conversation_id="group.test" if group else "alice",
        conversation_kind="group" if group else "direct",
    )
    runtime.receive_message(message)
    runtime.receive_message(message)
    await drain(runtime)
    runtime.client.send_read_receipt.assert_awaited_once_with(
        MESSAGE.account, number or "alice", MESSAGE.timestamp
    )
    assert runtime.receipts.sent == 1


@pytest.mark.parametrize(
    "message",
    [
        replace(MESSAGE, sender_uuid="stranger", sender_number="+999"),
        replace(MESSAGE, conversation_kind="group", conversation_id="group.other"),
        replace(MESSAGE, sender_number=MESSAGE.account),
        replace(MESSAGE, account="+999"),
        Reaction(
            MESSAGE.account,
            "alice",
            MESSAGE.sender_number,
            2,
            1000,
            "alice",
            "direct",
            "👍",
            "bob",
            None,
            "bob",
            900,
            False,
        ),
    ],
)
async def test_filtered_self_wrong_account_and_reactions(runtime, message):
    runtime.receive_message(message)
    await drain(runtime)
    runtime.client.send_read_receipt.assert_not_awaited()


@pytest.mark.parametrize("option", ["receive", "send_read_receipts"])
async def test_option_disabled(runtime, hass, option):
    with patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock):
        hass.config_entries.async_update_entry(
            runtime.entry, options={**runtime.entry.options, option: False}
        )
        runtime.receive_message(MESSAGE)
        await drain(runtime)
        await hass.async_block_till_done()
    runtime.client.send_read_receipt.assert_not_awaited()


async def test_assist_handled_without_event_permissions(runtime, hass):
    with patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock):
        hass.config_entries.async_update_entry(
            runtime.entry, options={**runtime.entry.options, "allowed_senders": []}
        )
        with patch.object(runtime.assist, "handle", return_value=True):
            runtime.receive_message(MESSAGE)
            await drain(runtime)
        await hass.async_block_till_done()
    runtime.client.send_read_receipt.assert_awaited_once()


@pytest.mark.parametrize("error", [SignalError("SECRET"), InvalidAuth("SECRET")])
async def test_receipt_errors_do_not_retry_or_block_receiving(runtime, error):
    runtime.client.send_read_receipt.side_effect = [error, None]
    with patch.object(runtime, "auth_failed") as auth:
        runtime.receive_message(MESSAGE)
        runtime.receive_message(replace(MESSAGE, timestamp=1001))
        await drain(runtime)
    assert runtime.client.send_read_receipt.await_count == 2
    assert runtime.receipts.failed == 1
    assert runtime.receipts.sent == 1
    assert auth.call_count == int(isinstance(error, InvalidAuth))
    assert runtime.last_received is not None


async def test_bounded_queue_and_unload(runtime, hass):
    cancelled = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime.client.send_read_receipt.side_effect = blocked
    runtime.receive_message(MESSAGE)
    for i in range(MAX_PENDING + 3):
        runtime.receive_message(replace(MESSAGE, timestamp=2000 + i))
    assert runtime.receipts.queue.qsize() == MAX_PENDING
    assert runtime.receipts.dropped == 3
    await hass.config_entries.async_unload(runtime.entry.entry_id)
    assert cancelled.is_set()
    assert runtime.receipts.queue.empty()
    assert runtime.receipts.task.done()
    runtime.receipts.handle(replace(MESSAGE, timestamp=9999))
    runtime.client.send_read_receipt.assert_awaited_once()
