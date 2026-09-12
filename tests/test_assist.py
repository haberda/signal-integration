"""Direct Assist routing, session isolation, bounded work and lifecycle."""

import asyncio
from dataclasses import replace
from unittest.mock import ANY, AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.signal_messenger_rest.assist import MAX_PENDING, SignalAssist
from custom_components.signal_messenger_rest.const import EVENT_MESSAGE
from custom_components.signal_messenger_rest.models import Message

ASSIST = {
    "enabled": True,
    "pipeline": "test-pipeline",
    "senders": ["alice", "bob"],
    "groups": ["group.test"],
    "prefix": "/assist",
    "direct_mode": "prefix",
    "conversation_timeout": 300,
    "publish_events": False,
}
MESSAGE = Message(
    "+12025550100",
    "alice",
    "+12025550101",
    2,
    1000,
    "alice",
    "direct",
    "/assist turn on the light",
    [],
    None,
)
TARGET = "custom_components.signal_messenger_rest.assist.run_text"


@pytest.fixture
async def runtime(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "assist": ASSIST}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    runtime.send_message = AsyncMock(return_value={"success": True})
    yield runtime
    if entry.state == ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)


async def drain(runtime):
    await runtime.assist.queue.join()
    if runtime.assist.task:
        await runtime.assist.task
    await runtime.assist.feedback_queue.join()
    if runtime.assist.feedback_task:
        await runtime.assist.feedback_task


async def test_direct_routing_and_dedup(runtime, hass):
    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    with patch(
        TARGET, new_callable=AsyncMock, return_value=("Turned on.", "conv-1")
    ) as run:
        runtime.receive_message(MESSAGE)
        runtime.receive_message(MESSAGE)
        await drain(runtime)
        run.assert_awaited_once_with(
            hass, "test-pipeline", "turn on the light", None, on_route=ANY
        )
        runtime.send_message.assert_awaited_once_with(["alice"], "Turned on.")
        assert not events
        assert runtime.assist.completed == 1


@pytest.mark.parametrize(
    "message",
    [
        replace(MESSAGE, sender_uuid="stranger", sender_number="+999"),
        replace(MESSAGE, conversation_kind="group", conversation_id="group.other"),
        replace(MESSAGE, text="ordinary conversation"),
        replace(MESSAGE, text="/assistant should not match"),
        replace(MESSAGE, sender_number="+12025550100"),
        replace(MESSAGE, text="", attachments=[{"id": "voice"}]),
        replace(MESSAGE, account="+999"),
    ],
)
async def test_denied_or_unaddressed_messages(runtime, message):
    with patch(TARGET, new_callable=AsyncMock) as run:
        assert not runtime.assist.handle(message)
        run.assert_not_awaited()


async def test_group_prefix_is_always_required(runtime):
    runtime.assist.settings = {**ASSIST, "direct_mode": "all"}
    group = replace(MESSAGE, conversation_kind="group", conversation_id="group.test")
    with patch(TARGET, new_callable=AsyncMock, return_value=("Done.", "group-session")):
        assert not runtime.assist.handle(replace(group, text="hello"))
        assert runtime.assist.handle(group)
        await drain(runtime)
        runtime.send_message.assert_awaited_once_with(
            ["group.test"], "Done.", quote_timestamp=1000, quote_author="+12025550101"
        )


async def test_sessions_isolated_reset_and_expiry(runtime):
    with (
        patch(
            TARGET,
            new_callable=AsyncMock,
            side_effect=[
                ("one", "s1"),
                ("two", "s1"),
                ("bob", "s2"),
                ("group", "s3"),
                ("reset", "s4"),
                ("expired", "s5"),
            ],
        ) as run,
        patch(
            "custom_components.signal_messenger_rest.assist.monotonic", return_value=10
        ) as now,
    ):
        for message in [
            MESSAGE,
            replace(MESSAGE, timestamp=1001),
            replace(MESSAGE, timestamp=1002, sender_uuid="bob"),
            replace(
                MESSAGE,
                timestamp=1003,
                conversation_kind="group",
                conversation_id="group.test",
            ),
            replace(MESSAGE, timestamp=1004, text="/assist /reset"),
            replace(MESSAGE, timestamp=1005),
        ]:
            runtime.assist.handle(message)
            await drain(runtime)
        assert [call.args[3] for call in run.call_args_list] == [
            None,
            "s1",
            None,
            None,
            None,
        ]
        now.return_value = 311
        runtime.assist.handle(replace(MESSAGE, timestamp=1006))
        await drain(runtime)
        assert run.call_args.args[3] is None


async def test_timeout_does_not_retry(runtime):
    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    with (
        patch(TARGET, side_effect=slow) as run,
        patch("custom_components.signal_messenger_rest.assist.RUN_TIMEOUT", 0.01),
    ):
        runtime.assist.handle(MESSAGE)
        await drain(runtime)
        assert run.await_count == 1
        assert runtime.assist.failed == 1
        assert "not retried" in runtime.send_message.call_args.args[1]
        assert not runtime.assist.sessions


async def test_worker_is_bounded_and_unload_cancels(runtime, hass):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with patch(TARGET, side_effect=slow) as run:
        runtime.assist.handle(MESSAGE)
        await started.wait()
        for i in range(MAX_PENDING + 3):
            runtime.assist.handle(replace(MESSAGE, timestamp=2000 + i))
        assert runtime.assist.queue.qsize() == MAX_PENDING
        assert runtime.assist.dropped == 3
        await hass.config_entries.async_unload(runtime.entry.entry_id)
        assert cancelled.is_set()
        assert runtime.assist.queue.empty()
        assert run.await_count == 1
        assert not runtime.assist.handle(replace(MESSAGE, timestamp=9999))
        assert all(
            "busy" in call.args[1] for call in runtime.send_message.await_args_list
        )
        assert runtime.assist.feedback_queue.empty()


async def test_opt_in_events_still_require_event_permissions(runtime, hass):
    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    runtime.assist.settings = {**ASSIST, "publish_events": True}
    hass.config_entries.async_update_entry(
        runtime.entry,
        options={**runtime.entry.options, "assist": runtime.assist.settings},
    )
    # Call the coordinator while the entry's pending reload is suppressed below.
    with (
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        patch(TARGET, new_callable=AsyncMock, return_value=("ok", "s1")),
    ):
        runtime.receive_message(MESSAGE)
        await drain(runtime)
        await hass.async_block_till_done()
        assert not events
        hass.config_entries.async_update_entry(
            runtime.entry,
            options={
                **runtime.entry.options,
                "allowed_senders": ["alice"],
            },
        )
        runtime.receive_message(replace(MESSAGE, timestamp=1001))
        await drain(runtime)
        await hass.async_block_till_done()
        assert len(events) == 1


async def test_exception_content_is_not_sent_and_worker_continues(runtime):
    with patch(
        TARGET, new_callable=AsyncMock, side_effect=[ValueError("SECRET"), ("ok", "s2")]
    ) as run:
        runtime.assist.handle(MESSAGE)
        runtime.assist.handle(replace(MESSAGE, timestamp=1001))
        await drain(runtime)
        assert run.await_count == 2
        assert "SECRET" not in str(runtime.send_message.call_args_list)
        assert runtime.assist.completed == 1


async def test_settings_disabled(runtime):
    runtime.assist = SignalAssist(runtime)
    runtime.assist.settings = {}
    assert not runtime.assist.handle(MESSAGE)


async def test_all_direct_messages_and_reset(runtime):
    runtime.assist.settings = {**ASSIST, "direct_mode": "all"}
    with patch(TARGET, new_callable=AsyncMock, return_value=("ok", "s1")) as run:
        runtime.assist.handle(replace(MESSAGE, text="turn on the light"))
        await drain(runtime)
        assert run.call_args.args[2] == "turn on the light"
        runtime.assist.handle(replace(MESSAGE, timestamp=1001, text="/reset"))
        await drain(runtime)
        assert run.await_count == 1
        assert not runtime.assist.sessions


async def test_oversized_and_stale_requests_are_not_executed(runtime):
    gate = asyncio.Event()

    async def wait_for_gate(*args, **kwargs):
        await gate.wait()
        return "ok", "s1"

    with (
        patch(TARGET, side_effect=wait_for_gate) as run,
        patch(
            "custom_components.signal_messenger_rest.assist.monotonic", return_value=10
        ) as now,
    ):
        runtime.assist.handle(replace(MESSAGE, text="/assist " + "x" * 4001))
        assert runtime.assist.dropped == 1
        runtime.assist.handle(replace(MESSAGE, timestamp=1001))
        runtime.assist.handle(replace(MESSAGE, timestamp=1002))
        now.return_value = 71
        gate.set()
        await drain(runtime)
        assert runtime.assist.dropped == 2
        run.assert_awaited_once()


async def test_failed_reply_does_not_repeat_pipeline(runtime):
    runtime.send_message.return_value = {"success": False}
    with patch(TARGET, new_callable=AsyncMock, return_value=("ok", "s1")) as run:
        runtime.assist.handle(MESSAGE)
        await drain(runtime)
        run.assert_awaited_once()
        runtime.send_message.assert_awaited_once()
        assert runtime.assist.failed == 1


async def test_route_diagnostics_do_not_include_conversation_data(runtime, hass):
    from custom_components.signal_messenger_rest.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    async def routed(*args, on_route):
        on_route(
            {
                "selected_agent_is_local": False,
                "prefer_local_intents": False,
                "processed_locally": False,
            }
        )
        return "Private reply", "private-conversation-id"

    with patch(TARGET, side_effect=routed):
        runtime.assist.handle(MESSAGE)
        await drain(runtime)
    diagnostics = await async_get_config_entry_diagnostics(hass, runtime.entry)
    assert diagnostics["assist_running_activation"] == "prefix"
    assert diagnostics["assist_configured_activation"] == "prefix"
    assert diagnostics["assist_pipeline_matches_options"] is True
    assert diagnostics["assist_last_route"] == {
        "selected_agent_is_local": False,
        "prefer_local_intents": False,
        "processed_locally": False,
    }
    for private in (
        "alice",
        "Private reply",
        "private-conversation-id",
        "test-pipeline",
    ):
        assert private not in str(diagnostics)


async def test_feedback_is_quoted_rate_limited_and_deduplicated(runtime):
    with patch(
        "custom_components.signal_messenger_rest.assist.monotonic", return_value=10
    ) as now:
        for i in range(12):
            message = replace(MESSAGE, timestamp=2000 + i, text="/assist " + "x" * 4001)
            assert runtime.assist.handle(message)
            assert runtime.assist.handle(message)
        await drain(runtime)
        assert runtime.assist.dropped == 12
        assert runtime.assist.rejected["oversized"] == 12
        assert runtime.assist.feedback_suppressed == 7
        assert runtime.send_message.await_count == 5
        call = runtime.send_message.await_args_list[0]
        assert "too long" in call.args[1]
        assert call.kwargs == {"quote_timestamp": 2000, "quote_author": "+12025550101"}
        now.return_value = 71
        runtime.assist.handle(replace(message, timestamp=3000))
        await drain(runtime)
        assert runtime.send_message.await_count == 6


async def test_feedback_failure_is_sanitized_and_not_retried(runtime):
    runtime.send_message.side_effect = ValueError("SECRET")
    runtime.assist.handle(replace(MESSAGE, text="/assist " + "x" * 4001))
    await drain(runtime)
    assert runtime.assist.failed == 1
    assert runtime.assist.last_error == "feedback_failed"
    runtime.send_message.assert_awaited_once()


async def test_unload_cancels_blocked_feedback(runtime):
    cancelled = asyncio.Event()

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime.send_message.side_effect = blocked
    for i in range(3):
        runtime.assist.handle(
            replace(MESSAGE, timestamp=2000 + i, text="/assist " + "x" * 4001)
        )
    await runtime.assist.stop()
    assert cancelled.is_set()
    assert runtime.assist.feedback_queue.empty()
    assert runtime.assist.feedback_task.done()
    runtime.send_message.assert_awaited_once()
