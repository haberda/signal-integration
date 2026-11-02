"""Bounded, best-effort read receipts for messages accepted by the integration."""

import asyncio
from collections import OrderedDict

from .api import InvalidAuth, SignalError
from .const import CONF_READ_RECEIPTS, CONF_RECEIVE
from .models import Message

MAX_PENDING = 128


class ReadReceipts:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.queue = asyncio.Queue(maxsize=MAX_PENDING)
        self.seen = OrderedDict()
        self.task = None
        self.closed = False
        self.sent = 0
        self.failed = 0
        self.dropped = 0

    def handle(self, message):
        options = self.coordinator.entry.options
        if (
            self.closed
            or not options.get(CONF_RECEIVE)
            or not options.get(CONF_READ_RECEIPTS, False)
            or not isinstance(message, Message)
            or message.account != self.coordinator.account
            or message.sender_number == self.coordinator.account
        ):
            return
        recipient = message.sender_number or message.sender_uuid
        if not recipient or message.timestamp <= 0:
            return
        key = (recipient, message.timestamp)
        if key in self.seen:
            return
        self.seen[key] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        if self.queue.full():
            self.dropped += 1
            return
        # Retain only receipt routing, not message content or attachments.
        self.queue.put_nowait(key)
        if self.task is None or self.task.done():
            self.task = self.coordinator.entry.async_create_background_task(
                self.coordinator.hass, self._work(), "Signal read receipts"
            )

    async def _work(self):
        while not self.queue.empty():
            recipient, timestamp = self.queue.get_nowait()
            try:
                # Include lock wait in the deadline; receipt traffic must be bounded.
                async with asyncio.timeout(15):
                    await self.coordinator.client.send_read_receipt(
                        self.coordinator.account, recipient, timestamp
                    )
            except InvalidAuth:
                self.failed += 1
                self.coordinator.auth_failed()
            except SignalError, TimeoutError:
                self.failed += 1
            else:
                self.sent += 1
            finally:
                self.queue.task_done()

    async def stop(self):
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.seen.clear()
