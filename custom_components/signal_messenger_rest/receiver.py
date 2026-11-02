"""Cancellable receiver with bounded deduplication and reconnect backoff."""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from random import uniform

from .api import InvalidAuth, SignalClient, SignalError
from .models import IncomingEvent, normalize_event


class Receiver:
    def __init__(
        self,
        client: SignalClient,
        account: str,
        interval: int,
        message_callback: Callable[[IncomingEvent], None],
        status_callback: Callable[[bool], None],
        auth_callback: Callable[[], None],
    ):
        self.client = client
        self.account = account
        self.interval = interval
        self.on_message = message_callback
        self.on_status = status_callback
        self.on_auth = auth_callback
        self.seen: OrderedDict[tuple, None] = OrderedDict()
        self.reconnects = 0
        self.ignored = 0

    def dispatch(self, raw) -> None:
        message = normalize_event(raw, self.account)
        if message is None:
            self.ignored += 1
            return
        if message.dedup_key in self.seen:
            return
        self.seen[message.dedup_key] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        self.on_message(message)

    async def run(self) -> None:
        delay = 1
        try:
            while True:
                started = asyncio.get_running_loop().time()
                try:
                    if self.client.mode in {"json-rpc", "json-rpc-native"}:
                        async for raw in self.client.messages(self.account):
                            if raw == {"_connected": True}:
                                self.on_status(True)
                            else:
                                self.dispatch(raw)
                        raise SignalError("Stream closed")
                    batch = await self.client.receive(self.account)
                    self.on_status(True)
                    for raw in batch:
                        self.dispatch(raw)
                    delay = 1
                    await asyncio.sleep(self.interval)
                    continue
                except InvalidAuth:
                    self.on_auth()
                    return
                except SignalError:
                    self.on_status(False)
                    self.reconnects += 1
                    if asyncio.get_running_loop().time() - started > 60:
                        delay = 1
                    await asyncio.sleep(delay + uniform(0, delay / 4))
                    delay = min(delay * 2, 60)
        finally:
            self.on_status(False)
