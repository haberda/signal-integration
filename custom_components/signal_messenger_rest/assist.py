"""Bounded, opt-in Signal text conversations with an existing Assist pipeline."""

import asyncio
from collections import OrderedDict
from time import monotonic

from homeassistant.core import Context
from homeassistant.helpers import chat_session

from .models import Message

MAX_PENDING = 16
MAX_SESSIONS = 64
MAX_INPUT = 4000
RUN_TIMEOUT = 60
QUEUE_TIMEOUT = 60


class AssistError(Exception):
    """A sanitized pipeline failure."""


async def run_text(hass, pipeline_id, text, conversation_id=None):
    """Run only intent processing, using the selected pipeline's agent/language."""
    if "assist_pipeline" not in hass.config.components:
        raise AssistError("Assist pipeline is not loaded")
    from homeassistant.components.assist_pipeline.pipeline import (
        PipelineEventType,
        PipelineInput,
        PipelineRun,
        PipelineStage,
        async_get_pipeline,
    )

    result = None
    failed = False

    def on_event(event):
        nonlocal result, failed
        if event.type == PipelineEventType.INTENT_END:
            result = (event.data or {}).get("intent_output")
        elif event.type == PipelineEventType.ERROR:
            failed = True

    pipeline = async_get_pipeline(hass, pipeline_id)
    with chat_session.async_get_chat_session(hass, conversation_id) as session:
        run = PipelineRun(
            hass=hass,
            context=Context(),
            pipeline=pipeline,
            start_stage=PipelineStage.INTENT,
            end_stage=PipelineStage.INTENT,
            event_callback=on_event,
        )
        await PipelineInput(run=run, session=session, intent_input=text).execute(
            validate=True
        )
        if failed or not isinstance(result, dict):
            raise AssistError("Assist did not complete")
        response = result.get("response", {})
        speech = response.get("speech", {}).get("plain", {}).get("speech")
        if not isinstance(speech, str) or not speech.strip():
            speech = (
                "Done."
                if response.get("response_type") == "action_done"
                else "Assist returned no text response."
            )
        return speech, result.get("conversation_id") or session.conversation_id


class SignalAssist:
    """One worker per account keeps commands ordered without blocking reception."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.settings = coordinator.entry.options.get("assist", {})
        self.queue = asyncio.Queue(maxsize=MAX_PENDING)
        self.task = None
        self.sessions = OrderedDict()
        self.seen = OrderedDict()
        self.closed = False
        self.completed = 0
        self.failed = 0
        self.dropped = 0

    def handle(self, message):
        """Return whether this message belongs exclusively to Assist by default."""
        if (
            self.closed
            or not self.settings.get("enabled", False)
            or not isinstance(message, Message)
            or message.account != self.coordinator.account
            or message.sender_number == self.coordinator.account
            or not message.allowed(
                self.settings.get("senders", []), self.settings.get("groups", [])
            )
        ):
            return False
        text = message.text.strip()
        if not text:
            return False
        prefix = self.settings.get("prefix", "/assist")
        prefixed = text == prefix or text.startswith(prefix + " ")
        if (
            message.conversation_kind == "group"
            or self.settings.get("direct_mode", "prefix") == "prefix"
        ):
            if not prefixed:
                return False
        if prefixed:
            text = text[len(prefix) :].strip()
        if message.dedup_key in self.seen:
            return True
        self.seen[message.dedup_key] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        if len(text) > MAX_INPUT or self.queue.full():
            self.dropped += 1
            return True
        self.queue.put_nowait((message, text, monotonic()))
        if self.task is None or self.task.done():
            self.task = self.coordinator.entry.async_create_background_task(
                self.coordinator.hass, self._work(), "Signal Assist"
            )
        return True

    async def _work(self):
        while not self.queue.empty():
            message, text, queued_at = self.queue.get_nowait()
            try:
                if monotonic() - queued_at > QUEUE_TIMEOUT:
                    self.dropped += 1
                    continue
                await self._process(message, text)
            finally:
                self.queue.task_done()

    async def _process(self, message, text):
        key = (message.conversation_id, message.sender)
        session = self.sessions.pop(key, None)
        conversation_id = None
        if session and monotonic() - session[1] < self.settings.get(
            "conversation_timeout", 300
        ):
            conversation_id = session[0]
        try:
            if text == "/reset":
                reply = "Started a new Assist conversation."
            elif not text:
                reply = "Send a command after the Assist prefix. Use /reset to start a new conversation."
            else:
                try:
                    async with asyncio.timeout(RUN_TIMEOUT):
                        reply, conversation_id = await run_text(
                            self.coordinator.hass,
                            self.settings["pipeline"],
                            text,
                            conversation_id,
                        )
                except Exception:
                    # Pipeline exceptions can contain prompts, names or provider
                    # credentials. Never log or echo their contents.
                    self.failed += 1
                    conversation_id = None
                    reply = "Assist could not complete this request. It was not retried; an action may already have happened."
                else:
                    self.completed += 1
                    self.sessions[key] = (conversation_id, monotonic())
                    if len(self.sessions) > MAX_SESSIONS:
                        self.sessions.popitem(last=False)
            fields = {}
            if message.conversation_kind == "group":
                fields = {
                    "quote_timestamp": message.timestamp,
                    "quote_author": message.sender_number or message.sender_uuid,
                }
            result = await self.coordinator.send_message(
                [message.conversation_id], reply[:10000], **fields
            )
            if not result["success"]:
                self.failed += 1
        except Exception:
            # Sending failures must not kill the worker or retry an action.
            self.failed += 1

    async def stop(self):
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.sessions.clear()
        self.seen.clear()
