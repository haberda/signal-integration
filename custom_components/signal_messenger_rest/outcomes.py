"""Classify v2 send responses without retaining recipient identities.

signal-cli-rest-api 0.101 and later answer HTTP 201 with the message
``timestamp`` and, when signal-cli reported a non-success result for any
recipient, ``errors.recipients[]`` holding ``number``/``uuid``/``username``
and signal-cli's result type as ``reason``. For a group this lists individual
members, so the message was still sent to the others.
"""

from collections import Counter
from uuid import UUID

REASONS = {
    "NETWORK_FAILURE",
    "UNREGISTERED_FAILURE",
    "IDENTITY_FAILURE",
    "RATE_LIMIT_FAILURE",
    "INVALID_PRE_KEY_FAILURE",
}
MAX_MEMBERS = 50


def redact(value) -> str:
    """Leave enough to recognize a contact, never a usable identifier."""
    if not isinstance(value, str) or not value:
        return "hidden"
    if value.startswith("+") and value[1:].isdigit() and len(value) > 6:
        return f"…{value[-2:]}"
    try:
        UUID(value)
    except ValueError:
        return "hidden"
    return f"{value[:8]}…"


def failed_members(result: dict) -> list[dict] | None:
    """Return redacted member failures, or None for an unrecognized report."""
    errors = result.get("errors")
    if not errors:
        return []
    recipients = errors.get("recipients") if isinstance(errors, dict) else None
    if not isinstance(recipients, list):
        return None
    members = []
    for item in recipients[:MAX_MEMBERS]:
        if not isinstance(item, dict):
            return None
        reason = item.get("reason")
        members.append(
            {
                "member": redact(item.get("number") or item.get("uuid")),
                # Only known types are echoed; anything else is not passed through.
                "reason": reason.lower() if reason in REASONS else "unknown_failure",
            }
        )
    return members


def reason_counts(results: list[dict]) -> dict[str, int]:
    """Count member failures by type across destinations; no identities."""
    return dict(
        Counter(
            member["reason"]
            for result in results
            for member in result.get("failed_members", [])
        )
    )


def overall_status(results: list[dict]) -> str:
    statuses = {result["status"] for result in results}
    if "failed" in statuses:
        return "failed"
    return "partial" if "partial" in statuses else "sent"
