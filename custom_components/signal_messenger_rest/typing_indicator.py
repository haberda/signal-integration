"""Best-effort typing feedback scoped to one active Assist request."""

import asyncio
from contextlib import asynccontextmanager

REFRESH_INTERVAL = 8
REQUEST_TIMEOUT = 2


async def _send(coordinator, recipient, active):
    try:
        # Include lock acquisition, so a busy backend cannot delay cleanup forever.
        async with asyncio.timeout(REQUEST_TIMEOUT):
            await coordinator.client.set_typing(coordinator.account, recipient, active)
    except Exception:
        # Indicator failures must not affect Assist or expose backend error bodies.
        return False
    return True


async def _refresh(coordinator, recipient):
    while await _send(coordinator, recipient, True):  # noqa: ASYNC110 - periodic protocol refresh
        await asyncio.sleep(REFRESH_INTERVAL)


@asynccontextmanager
async def typing_indicator(coordinator, recipient, enabled):
    if not enabled:
        yield
        return
    task = coordinator.entry.async_create_background_task(
        coordinator.hass, _refresh(coordinator, recipient), "Signal Assist typing"
    )
    try:
        yield
    finally:
        # Prevent a late refresh from restarting typing after the stop request.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _send(coordinator, recipient, False)
