"""Send an alert and correlate an authorized reaction without a listener race."""

import asyncio
from collections import deque
from uuid import UUID

from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError

from .const import CONF_RECEIVE, EVENT_REACTION


def matches_ack(
    data: dict,
    *,
    entry_id: str,
    account: str,
    account_author: str,
    recipient: str,
    timestamp: int,
    emoji: str,
) -> bool:
    """Require account, conversation, author, message reference and positive reaction."""
    if (
        data.get("config_entry_id") != entry_id
        or data.get("account") != account
        or data.get("target_timestamp") != timestamp
        or data.get("emoji") != emoji
        or data.get("removed") is not False
    ):
        return False
    authors = {account, account_author} - {""}
    if not authors.intersection(
        {
            data.get("target_author"),
            data.get("target_author_number"),
            data.get("target_author_uuid"),
        }
    ):
        return False
    if recipient.startswith("group."):
        return (
            data.get("conversation_kind") == "group"
            and data.get("conversation_id") == recipient
        )
    return data.get("conversation_kind") == "direct" and recipient in {
        data.get("sender_number"),
        data.get("sender_uuid"),
        data.get("conversation_id"),
    }


async def send_alert(
    runtime,
    *,
    recipient: str,
    message: str,
    emoji: str = "✅",
    account_author: str = "",
    expiry: int = 600,
    reminder_interval: int = 120,
) -> dict:
    """Keep one original reference; reminder messages do not replace it."""
    if not runtime.entry.options.get(CONF_RECEIVE):
        raise ServiceValidationError(
            "Enable receiving before sending an acknowledgment alert"
        )
    if not recipient.startswith(("+", "group.")):
        try:
            UUID(recipient)
        except ValueError:
            raise ServiceValidationError(
                "Alerts require a phone number, UUID or group ID, not a username"
            ) from None
    task = asyncio.current_task()
    if len(runtime.alert_tasks) >= 16:
        raise ServiceValidationError(
            "At most sixteen acknowledgment alerts may wait per account"
        )
    runtime.alert_tasks.add(task)
    acknowledged = asyncio.Event()
    early = deque(maxlen=32)
    reference = None
    loop = asyncio.get_running_loop()
    deadline = None

    @callback
    def react(event):
        data = event.data
        if reference is None:
            if data.get("config_entry_id") == runtime.entry.entry_id:
                early.append(data)
        elif loop.time() < deadline and matches_ack(
            data,
            entry_id=runtime.entry.entry_id,
            account=runtime.account,
            account_author=account_author,
            recipient=recipient,
            timestamp=reference,
            emoji=emoji,
        ):
            acknowledged.set()

    remove_listener = runtime.hass.bus.async_listen(EVENT_REACTION, react)
    try:
        result = await runtime.send_message([recipient], message)
        if not result["success"]:
            raise ServiceValidationError(
                "Alert send failed or is uncertain; no reminders started"
            )
        try:
            reference = int(result["results"][0]["timestamp"])
        except ValueError, TypeError:
            raise ServiceValidationError(
                "Alert returned an invalid reference; no reminders started"
            ) from None
        deadline = loop.time() + expiry
        for data in early:
            if matches_ack(
                data,
                entry_id=runtime.entry.entry_id,
                account=runtime.account,
                account_author=account_author,
                recipient=recipient,
                timestamp=reference,
                emoji=emoji,
            ):
                acknowledged.set()
                break
        early.clear()
        while not acknowledged.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(min(reminder_interval, remaining)):
                    await acknowledged.wait()
            except TimeoutError:
                if loop.time() >= deadline:
                    break
                # Quote the original alert. A failure ends the action rather than retrying.
                result = await runtime.send_message(
                    [recipient],
                    f"Reminder: {message}",
                    quote_timestamp=reference,
                    quote_author=account_author or runtime.account,
                )
                if not result["success"]:
                    raise ServiceValidationError(
                        "Reminder failed or is uncertain; remaining reminders stopped"
                    ) from None
        return {
            "acknowledged": acknowledged.is_set(),
            "reason": "acknowledged" if acknowledged.is_set() else "expired",
            "timestamp": str(reference),
        }
    finally:
        remove_listener()
        runtime.alert_tasks.discard(task)
