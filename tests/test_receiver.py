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
