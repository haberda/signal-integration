"""Reconnect, polling and shutdown behavior."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.signal_messenger_rest.api import CannotConnect, InvalidAuth
from custom_components.signal_messenger_rest.receiver import Receiver

from .test_models import RAW


async def test_polling_batch_and_cancel():
    client = Mock(mode="native")
    client.receive = AsyncMock(return_value=[RAW, RAW])
    message = Mock()
    status = Mock()
    receiver = Receiver(client, "+12025550100", 10, message, status, Mock())
    received = asyncio.Event()

    async def sleep(_):
        received.set()
        await asyncio.Event().wait()

    with patch("custom_components.signal_messenger_rest.receiver.asyncio.sleep", sleep):
        task = asyncio.create_task(receiver.run())
        await received.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert message.call_count == 1
    assert client.receive.await_count == 1
    assert status.call_args.args == (False,)


async def test_reconnect_backoff_then_auth_stops():
    client = Mock(mode="native")
    client.receive = AsyncMock(
        side_effect=[CannotConnect(), CannotConnect(), InvalidAuth()]
    )
    auth = Mock()
    receiver = Receiver(client, "+12025550100", 10, Mock(), Mock(), auth)
    with (
        patch(
            "custom_components.signal_messenger_rest.receiver.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
        patch(
            "custom_components.signal_messenger_rest.receiver.uniform", return_value=0
        ),
    ):
        await receiver.run()
    assert [c.args[0] for c in sleep.call_args_list] == [1, 2]
    assert receiver.reconnects == 2
    auth.assert_called_once()


async def test_handler_failure_is_counted_without_ending_reception(caplog):
    second = {**RAW, "envelope": {**RAW["envelope"], "timestamp": 1700000009999}}
    second["envelope"]["dataMessage"] = {
        **RAW["envelope"]["dataMessage"],
        "timestamp": 1700000009999,
    }
    client = Mock(mode="native")
    client.receive = AsyncMock(side_effect=[[RAW, RAW, second], InvalidAuth()])
    message = Mock(side_effect=[RuntimeError("private body"), None])
    receiver = Receiver(client, "+12025550100", 10, message, Mock(), Mock())
    with patch(
        "custom_components.signal_messenger_rest.receiver.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await receiver.run()
    # The failed event is not redelivered, and the next one is still handled.
    assert message.call_count == 2
    assert client.receive.await_count == 2
    assert receiver.dispatch_errors == 1
    assert "RuntimeError" in caplog.text
    assert "private body" not in caplog.text
