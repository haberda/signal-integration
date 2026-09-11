"""Reaction contract fixtures based on signal-cli v0.14.5 JsonReaction.

Synthetic identities/content; not captured from a live Signal account.
"""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from aiohttp import web
from homeassistant.exceptions import ServiceValidationError

from custom_components.signal_messenger_rest.api import SignalClient
from custom_components.signal_messenger_rest.const import (
    DOMAIN,
    EVENT_MESSAGE,
    EVENT_REACTION,
)
from custom_components.signal_messenger_rest.models import Reaction, normalize_event
from custom_components.signal_messenger_rest.receiver import Receiver

from .test_models import RAW


def reaction_raw(**fields):
    raw = deepcopy(RAW)
    raw["envelope"]["dataMessage"] = {
        "timestamp": 2000,
        "reaction": {
            "emoji": "✅",
            "targetAuthor": "+12025550100",
            "targetAuthorNumber": "+12025550100",
            "targetAuthorUuid": "account-uuid",
            "targetSentTimestamp": 1000,
            "isRemove": False,
            **fields,
        },
    }
    return raw


def test_normalize_and_deduplicate():
    raw = reaction_raw()
    event = normalize_event(raw, RAW["account"])
    assert isinstance(event, Reaction)
    assert event.target_timestamp == 1000
    assert event.target_author == "account-uuid"
    assert event.allowed(["sender-uuid"], [])
    assert "text" not in event.payload()
    callback = Mock()
    receiver = Receiver(Mock(), RAW["account"], 10, callback, Mock(), Mock())
    for value in [raw, raw, reaction_raw(isRemove=True), reaction_raw(emoji="👍")]:
        receiver.dispatch(value)
    assert callback.call_count == 3


@pytest.mark.parametrize(
    "fields",
    [
        {"isRemove": "false"},
        {"targetSentTimestamp": True},
        {"targetSentTimestamp": 0},
        {"emoji": None},
        {"targetAuthor": None, "targetAuthorNumber": None, "targetAuthorUuid": None},
    ],
)
def test_malformed_reaction(fields):
    assert normalize_event(reaction_raw(**fields), RAW["account"]) is None


def test_legacy_author_and_group():
    raw = reaction_raw(targetAuthorUuid=None, targetAuthorNumber=None)
    raw["envelope"]["dataMessage"]["groupInfo"] = {"groupId": "YWJj"}
    event = normalize_event(raw, RAW["account"])
    assert event.target_author_number == RAW["account"]
    assert event.conversation_id == "group.WVdKag=="
    assert not event.allowed(["sender-uuid"], [])
    assert event.allowed(["sender-uuid"], [event.conversation_id])
    raw["envelope"]["syncMessage"] = {}
    assert normalize_event(raw, RAW["account"]) is None


@pytest.mark.usefixtures("socket_enabled")
async def test_reaction_http(aiohttp_server):
    requests = []

    async def handler(request):
        requests.append((request.method, await request.json()))
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/v1/reactions/{account}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        await client.react(RAW["account"], "group.test", "sender-uuid", 1000, "✅")
        await client.react(
            RAW["account"], "group.test", "sender-uuid", 1000, remove=True
        )
    assert requests == [
        (
            "POST",
            {
                "recipient": "group.test",
                "target_author": "sender-uuid",
                "timestamp": 1000,
                "reaction": "✅",
            },
        ),
        (
            "DELETE",
            {
                "recipient": "group.test",
                "target_author": "sender-uuid",
                "timestamp": 1000,
                "reaction": "",
            },
        ),
    ]


async def test_reaction_services_and_filtered_events(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "allowed_senders": ["sender-uuid"]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    events, messages = [], []
    hass.bus.async_listen(EVENT_REACTION, events.append)
    hass.bus.async_listen(EVENT_MESSAGE, messages.append)
    runtime = entry.runtime_data
    runtime.receive_message(normalize_event(reaction_raw(), RAW["account"]))
    blocked = reaction_raw()
    blocked["envelope"]["sourceUuid"] = "other"
    runtime.receive_message(normalize_event(blocked, RAW["account"]))
    await hass.async_block_till_done()
    assert len(events) == 1 and not messages
    with patch.object(runtime.client, "react", new_callable=AsyncMock) as react:
        data = {
            "recipient": "group.test",
            "target_author": "sender-uuid",
            "timestamp": 1000,
            "emoji": "✅",
        }
        await hass.services.async_call(DOMAIN, "send_reaction", data, blocking=True)
        assert react.call_args.kwargs == {"remove": False}
        await hass.services.async_call(
            DOMAIN,
            "remove_reaction",
            {k: v for k, v in data.items() if k != "emoji"},
            blocking=True,
        )
        assert react.call_args.kwargs == {"remove": True}
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "send_reaction", data, blocking=True)
