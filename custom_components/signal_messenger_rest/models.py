"""Normalize incoming messages without exposing raw envelopes."""

import base64
from dataclasses import asdict, dataclass
from typing import Any


def group_recipient(internal_id: str) -> str:
    """REST IDs wrap signal-cli's base64 ID in another base64 layer."""
    return "group." + base64.b64encode(internal_id.encode()).decode()


@dataclass(frozen=True)
class Message:
    account: str
    sender_uuid: str | None
    sender_number: str | None
    source_device: int | None
    timestamp: int
    conversation_id: str
    conversation_kind: str
    text: str
    attachments: list[dict]
    quote: dict | None

    @property
    def sender(self) -> str:
        return self.sender_uuid or self.sender_number or ""

    @property
    def dedup_key(self) -> tuple:
        return (
            self.account,
            self.sender,
            self.source_device,
            self.timestamp,
            self.conversation_id,
            "message",
        )

    def allowed(self, senders: list[str], groups: list[str]) -> bool:
        # A group alone is never authorization to act as any of its members.
        if not any(s and s in senders for s in (self.sender_uuid, self.sender_number)):
            return False
        return self.conversation_kind == "direct" or self.conversation_id in groups

    def payload(self) -> dict:
        return {"schema_version": 1, **asdict(self)}


def normalize_message(raw: Any, account: str) -> Message | None:
    """Accept REST/push envelopes. Ignore sync echoes and non-message events."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("params"), dict):
        raw = raw["params"]
    if raw.get("account", account) != account:
        return None
    envelope = raw.get("envelope")
    if not isinstance(envelope, dict) or "syncMessage" in envelope:
        return None
    data = envelope.get("dataMessage")
    if not isinstance(data, dict):
        return None
    if any(
        data.get(key) is not None
        for key in (
            "reaction",
            "remoteDelete",
            "pollCreate",
            "pollVote",
            "pollTerminate",
        )
    ):
        return None
    text = data.get("message")
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list):
        return None
    if not isinstance(text, str):
        text = ""
    if not text and not attachments:
        return None
    timestamp = data.get("timestamp", envelope.get("timestamp"))
    if type(timestamp) is not int or timestamp <= 0:
        return None
    number = envelope.get("sourceNumber")
    uuid = envelope.get("sourceUuid")
    source = envelope.get("source")
    if not number and isinstance(source, str) and source.startswith("+"):
        number = source
    if not uuid and isinstance(source, str) and not source.startswith("+"):
        uuid = source
    number = number if isinstance(number, str) and number else None
    uuid = uuid if isinstance(uuid, str) and uuid else None
    if not number and not uuid:
        return None
    group = data.get("groupInfo")
    if group is None:
        group = data.get("groupV2")
    if group is not None:
        # Never misclassify a malformed group message as an authorized direct message.
        if (
            not isinstance(group, dict)
            or not isinstance(group.get("groupId"), str)
            or not group["groupId"]
        ):
            return None
        conversation = group_recipient(group["groupId"])
        kind = "group"
    else:
        conversation = number or uuid
        kind = "direct"
    safe_attachments = []
    for attachment in attachments[:20]:
        if isinstance(attachment, dict):
            safe_attachments.append(
                {
                    k: v
                    for k, v in attachment.items()
                    if k in {"id", "contentType", "filename", "size", "width", "height"}
                    and isinstance(v, (str, int))
                }
            )
    quoted = data.get("quote")
    safe_quote = None
    if isinstance(quoted, dict):
        safe_quote = {
            k: v
            for k, v in quoted.items()
            if k in {"id", "author", "text"} and isinstance(v, (str, int))
        }
    device = envelope.get("sourceDevice")
    return Message(
        account,
        uuid,
        number,
        device if type(device) is int else None,
        timestamp,
        conversation,
        kind,
        text,
        safe_attachments,
        safe_quote,
    )
