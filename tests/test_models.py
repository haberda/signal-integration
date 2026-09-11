"""Receive identity, privacy and repeat-message behavior."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from custom_components.signal_messenger_rest.models import (
    normalize_message,
)
from custom_components.signal_messenger_rest.receiver import Receiver

RAW = {
    "account": "+12025550100",
    "envelope": {
        "sourceUuid": "sender-uuid",
        "sourceNumber": "+12025550101",
        "sourceDevice": 1,
        "timestamp": 1000,
        "dataMessage": {"timestamp": 1000, "message": "status", "attachments": []},
    },
}


def test_identity_permissions_and_group_conversion():
    msg = normalize_message(RAW, "+12025550100")
    assert msg.allowed(["sender-uuid"], [])
    assert not msg.allowed([], [])
    raw = deepcopy(RAW)
    raw["envelope"]["sourceNumber"] = None
    raw["envelope"]["dataMessage"]["groupInfo"] = {"groupId": "YWJj"}
    msg = normalize_message(raw, "+12025550100")
    assert msg.conversation_id == "group.WVdKag=="
    assert msg.allowed(["sender-uuid"], [msg.conversation_id])
    assert not msg.allowed(["sender-uuid"], [])
    assert not msg.allowed([], [msg.conversation_id])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(account="other"),
        lambda r: r["envelope"].update(syncMessage={}),
        lambda r: r["envelope"]["dataMessage"].update(reaction={}),
        lambda r: r["envelope"]["dataMessage"].update(groupInfo={"groupId": None}),
        lambda r: r["envelope"]["dataMessage"].update(timestamp="bad"),
        lambda r: r["envelope"]["dataMessage"].update(attachments="bad"),
    ],
)
def test_ignored_envelopes(mutation):
    raw = deepcopy(RAW)
    mutation(raw)
    assert normalize_message(raw, "+12025550100") is None


def test_dedup_does_not_drop_repeated_text():
    callback = Mock()
    receiver = Receiver(Mock(), "+12025550100", 10, callback, Mock(), Mock())
    receiver.dispatch(RAW)
    receiver.dispatch(RAW)
    other = deepcopy(RAW)
    other["envelope"]["dataMessage"]["timestamp"] += 1
    receiver.dispatch(other)
    assert callback.call_count == 2
    for index in range(3000):
        other["envelope"]["dataMessage"]["timestamp"] = index + 2000
        receiver.dispatch(other)
    assert len(receiver.seen) == 2048
