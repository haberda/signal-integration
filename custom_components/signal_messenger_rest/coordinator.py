"""Account runtime, health updates and message dispatch."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import InvalidAuth, SignalClient, SignalError
from .assist import SignalAssist
from .attachments import encode_attachments
from .const import (
    CONF_ACCOUNT,
    CONF_GROUPS,
    CONF_SENDERS,
    DOMAIN,
    EVENT_MESSAGE,
    EVENT_REACTION,
    MODES,
    SEND_DEADLINE,
)
from .models import IncomingEvent, Reaction
from .outcomes import failed_members, overall_status, reason_counts
from .receipts import ReadReceipts

_LOGGER = logging.getLogger(__name__)


class SignalCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: SignalClient, about: dict
    ):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
        )
        self.entry = entry
        self.client = client
        self.account = entry.data[CONF_ACCOUNT]
        self.data = about
        self.filtered_events = 0
        self.connected = False
        self.last_received = None
        self.last_sent = None
        self.last_send_outcome = None
        self.identity_failures: dict[str, list[str]] = {}
        self.receiver = None
        self.receiver_task = None
        self.send_lock = asyncio.Lock()
        self.alert_tasks: set[asyncio.Task] = set()
        self.assist = SignalAssist(self)
        self.receipts = ReadReceipts(self)

    @property
    def message_signal(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_message"

    async def _async_update_data(self) -> dict:
        try:
            async with self.client.lock:
                about = await self.client.request("GET", "v1/about", request_timeout=15)
            if not isinstance(about, dict) or about.get("mode") not in MODES:
                raise SignalError("Invalid server metadata")
            if about["mode"] != self.client.mode:
                # Reload closes the old receiver before selecting its new transport.
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.entry.entry_id)
                )
            return about
        except InvalidAuth:
            raise ConfigEntryAuthFailed("API authentication failed") from None
        except SignalError:
            raise UpdateFailed("Cannot reach Signal API") from None

    @callback
    def receive_status(self, connected: bool) -> None:
        if self.connected != connected:
            self.connected = connected
            self.async_update_listeners()

    @callback
    def auth_failed(self) -> None:
        self.entry.async_start_reauth(self.hass)

    @callback
    def receive_message(self, message: IncomingEvent) -> None:
        options = self.entry.options
        handled = self.assist.handle(message)
        event_allowed = message.allowed(
            options.get(CONF_SENDERS, []), options.get(CONF_GROUPS, [])
        )
        if not event_allowed and not handled:
            self.filtered_events += 1
            return
        self.receipts.handle(message)
        self.last_received = dt_util.utcnow()
        if not event_allowed or (
            handled and not options.get("assist", {}).get("publish_events", False)
        ):
            self.async_update_listeners()
            return
        self.hass.bus.async_fire(
            EVENT_REACTION if isinstance(message, Reaction) else EVENT_MESSAGE,
            {
                **message.payload(),
                "config_entry_id": self.entry.entry_id,
                "received_at": self.last_received.isoformat(),
            },
        )
        async_dispatcher_send(
            self.hass,
            self.message_signal,
            "reaction_received"
            if isinstance(message, Reaction)
            else "message_received",
        )
        self.async_update_listeners()

    async def send_message(
        self,
        recipients: list[str],
        message: str,
        *,
        title: str | None = None,
        attachments: list[str] | None = None,
        urls: list[str] | None = None,
        text_mode: str = "normal",
        quote_timestamp: int | None = None,
        quote_author: str | None = None,
        quote_message: str | None = None,
    ) -> dict:
        recipients = list(dict.fromkeys(r.strip() for r in recipients))
        if not recipients or len(recipients) > 20 or any(not r for r in recipients):
            raise ServiceValidationError("Provide between one and twenty destinations")
        if (quote_timestamp is None) != (quote_author is None):
            raise ServiceValidationError(
                "A reply requires both quote_timestamp and quote_author"
            )
        if quote_message is not None and quote_timestamp is None:
            raise ServiceValidationError("quote_message requires a quote reference")
        message = f"{title}\n\n{message}" if title else message
        if not message and not attachments and not urls:
            raise ServiceValidationError("Provide a message or attachment")
        if len(message) > 10000:
            raise ServiceValidationError("Message exceeds 10000 characters")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SEND_DEADLINE
        # Bound concurrent encoded payloads to one per account.
        try:
            async with asyncio.timeout_at(deadline):
                await self.send_lock.acquire()
        except TimeoutError:
            raise ServiceValidationError(
                "Signal was busy with earlier sends; nothing was sent"
            ) from None
        try:
            fields = {"text_mode": text_mode}
            try:
                async with asyncio.timeout_at(deadline):
                    encoded = await encode_attachments(
                        self.hass, attachments or [], urls or []
                    )
            except TimeoutError:
                raise ServiceValidationError(
                    "Attachments were not ready in time; nothing was sent"
                ) from None
            if encoded:
                fields["base64_attachments"] = encoded
            if quote_timestamp is not None:
                fields.update(
                    quote_timestamp=quote_timestamp, quote_author=quote_author
                )
                if quote_message is not None:
                    fields["quote_message"] = quote_message
            results = []
            for recipient in recipients:
                if loop.time() >= deadline:
                    result = {"error": "Not attempted; the send time limit was reached"}
                else:
                    result = await self._send_one(recipient, message, fields, deadline)
                result["success"] = "error" not in result
                result["status"] = (
                    "failed"
                    if "error" in result
                    else "partial"
                    if result.get("failed_members")
                    else "sent"
                )
                results.append({"recipient": recipient, **result})
                if result["success"]:
                    self.last_sent = dt_util.utcnow()
                self._track_identity_failures(recipient, result)
            self._record_outcome(results)
            self.async_update_listeners()
        finally:
            self.send_lock.release()
        return {
            "success": all(r["success"] for r in results),
            "partial": any(r["status"] == "partial" for r in results),
            "results": results,
        }

    async def _send_one(
        self, recipient: str, message: str, fields: dict, deadline: float
    ) -> dict:
        """Send to one destination; an ``error`` key marks it as failed."""
        try:
            async with asyncio.timeout_at(deadline):
                result = await self.client.send(
                    self.account, [recipient], message, **fields
                )
        except InvalidAuth:
            self.auth_failed()
            return {"error": "API authentication failed"}
        except SignalError as err:
            return {"error": str(err)}
        except TimeoutError:
            return {
                "error": "Send time limit reached; this send may already have succeeded"
            }
        sent = {"timestamp": str(result["timestamp"])}
        members = failed_members(result)
        if members is None:
            return {**sent, "error": "Backend reported a recipient failure"}
        if not members:
            return sent
        sent["failed_members"] = members
        if recipient.startswith("group."):
            # The backend lists only members that failed; the message was sent.
            return sent
        reasons = ", ".join(sorted({member["reason"] for member in members}))
        return {
            **sent,
            "error": f"Signal could not deliver to this recipient ({reasons})",
        }

    def _record_outcome(self, results: list[dict]) -> None:
        """Keep a content-free summary for diagnostics and warn about failures."""
        counts = reason_counts(results)
        self.last_send_outcome = {
            "status": overall_status(results),
            "destinations": len(results),
            "failed_destinations": sum(not r["success"] for r in results),
            "failed_members": counts,
        }
        if counts:
            _LOGGER.warning(
                "Signal reported recipient failures for a send (%s); "
                "messages to groups were still sent to the other members",
                ", ".join(
                    f"{reason}: {count}" for reason, count in sorted(counts.items())
                ),
            )

    def _track_identity_failures(self, recipient: str, result: dict) -> None:
        """Raise a repair for changed safety numbers; never trust automatically."""
        untrusted = [
            member["member"]
            for member in result.get("failed_members", [])
            if member["reason"] == "identity_failure"
        ]
        if untrusted:
            if self.identity_failures.get(recipient) == untrusted:
                return
            if (
                recipient not in self.identity_failures
                and len(self.identity_failures) >= 20
            ):
                return
            self.identity_failures[recipient] = untrusted
        elif result["status"] == "sent" and recipient in self.identity_failures:
            del self.identity_failures[recipient]
        else:
            return
        members = sorted(
            {m for found in self.identity_failures.values() for m in found}
        )
        if not members:
            self.clear_identity_issue()
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.identity_issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="untrusted_identity",
            translation_placeholders={
                "account": self.entry.title,
                "members": ", ".join(members[:10]),
            },
        )

    @property
    def identity_issue_id(self) -> str:
        return f"untrusted_identity_{self.entry.entry_id}"

    @callback
    def clear_identity_issue(self) -> None:
        self.identity_failures.clear()
        ir.async_delete_issue(self.hass, DOMAIN, self.identity_issue_id)

    async def react(
        self,
        recipient: str,
        target_author: str,
        timestamp: int,
        emoji: str = "",
        *,
        remove: bool = False,
    ) -> None:
        try:
            await self.client.react(
                self.account, recipient, target_author, timestamp, emoji, remove=remove
            )
        except InvalidAuth:
            self.auth_failed()
            raise ServiceValidationError("API authentication failed") from None
        except SignalError as err:
            raise ServiceValidationError(str(err)) from None

    async def stop_alerts(self) -> None:
        tasks = list(self.alert_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
