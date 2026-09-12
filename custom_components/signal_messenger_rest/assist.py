"""Bounded, opt-in Signal text conversations with an existing Assist pipeline."""

import asyncio
from collections import OrderedDict, deque
from time import monotonic

from homeassistant.core import Context
from homeassistant.helpers import chat_session

from .models import Message

MAX_PENDING = 16
MAX_SESSIONS = 64
MAX_INPUT = 4000
RUN_TIMEOUT = 60
QUEUE_TIMEOUT = 60
MAX_FEEDBACK_PENDING = 8
FEEDBACK_BURST = 5
FEEDBACK_WINDOW = 60
FEEDBACK_TEXT = {
    "busy": "Assist is busy and could not accept this request. It was not executed. Please try again shortly.",
    "oversized": "This Assist request is too long. Keep it within 4,000 characters after the prefix and send it again. It was not executed.",
    "expired": "This Assist request waited too long and expired before processing. It was not executed. Please send it again if still needed.",
}


class AssistError(Exception):
    """A sanitized pipeline failure."""


def pipeline_signature(hass, pipeline_id):
    """Invalidate Signal context when the selected pipeline's routing changes."""
    if "assist_pipeline" not in hass.config.components:
        return None
    from homeassistant.components.assist_pipeline.pipeline import async_get_pipeline

    pipeline = async_get_pipeline(hass, pipeline_id)
    return (
        pipeline.id,
        pipeline.conversation_engine,
        pipeline.conversation_language,
        pipeline.language,
        pipeline.stt_language,
        pipeline.tts_language,
        pipeline.prefer_local_intents,
    )


async def run_text(hass, pipeline_id, text, conversation_id=None, *, on_route=None):
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
            if on_route is not None:
                on_route(
                    {
                        "selected_agent_is_local": pipeline.conversation_engine
                        in (
                            None,
                            "",
                            "homeassistant",
                            "conversation.home_assistant",
                        ),
                        "prefer_local_intents": pipeline.prefer_local_intents,
                        "processed_locally": (event.data or {}).get(
                            "processed_locally"
                        ),
                    }
                )
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
        # HA's pipeline can run sentence triggers even with local matching
        # disabled. Use its agent-only path to honor the operator's preference.
        # Covered against the real pipeline, including consecutive LLM turns.
        run._intent_agent_only = not pipeline.prefer_local_intents
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
        self.last_route = {}
        self.active = False
        self.last_error = "none"
        self.rejected = dict.fromkeys(FEEDBACK_TEXT, 0)
        self.feedback_queue = asyncio.Queue(maxsize=MAX_FEEDBACK_PENDING)
        self.feedback_task = None
        self.feedback_times = deque()
        self.feedback_suppressed = 0

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
        if len(text) > MAX_INPUT:
            self._reject(message, "oversized")
            return True
        if self.queue.full():
            self._reject(message, "busy")
            return True
        self.queue.put_nowait((message, text, monotonic()))
        if self.task is None or self.task.done():
            self.task = self.coordinator.entry.async_create_background_task(
                self.coordinator.hass, self._work(), "Signal Assist"
            )
        self._updated()
        return True

    @property
    def status(self):
        """Content-free operational status, including disabled entries."""
        if self.closed:
            return "stopped"
        if not self.settings.get("enabled"):
            return "disabled"
        if not self.coordinator.entry.options.get("receive"):
            return "receiving_disabled"
        if not self.coordinator.last_update_success or not self.coordinator.connected:
            return "disconnected"
        try:
            if (
                pipeline_signature(self.coordinator.hass, self.settings.get("pipeline"))
                is None
            ):
                return "pipeline_unavailable"
        except Exception:
            return "pipeline_unavailable"
        return "processing" if self.active else "idle"

    def _updated(self):
        self.coordinator.async_update_listeners()

    def _reject(self, message, reason):
        self.dropped += 1
        self.rejected[reason] += 1
        now = monotonic()
        while self.feedback_times and now - self.feedback_times[0] >= FEEDBACK_WINDOW:
            self.feedback_times.popleft()
        if len(self.feedback_times) >= FEEDBACK_BURST or self.feedback_queue.full():
            self.feedback_suppressed += 1
        else:
            self.feedback_times.append(now)
            # Keep routing references only, never the rejected body or attachments.
            self.feedback_queue.put_nowait(
                (
                    message.conversation_id,
                    message.sender_number or message.sender_uuid,
                    message.timestamp,
                    reason,
                    now,
                )
            )
            if self.feedback_task is None or self.feedback_task.done():
                self.feedback_task = (
                    self.coordinator.entry.async_create_background_task(
                        self.coordinator.hass,
                        self._feedback(),
                        "Signal Assist feedback",
                    )
                )
        self._updated()

    async def _feedback(self):
        while not self.feedback_queue.empty():
            recipient, author, timestamp, reason, queued_at = (
                self.feedback_queue.get_nowait()
            )
            try:
                if monotonic() - queued_at > QUEUE_TIMEOUT:
                    self.feedback_suppressed += 1
                    continue
                try:
                    result = await self.coordinator.send_message(
                        [recipient],
                        FEEDBACK_TEXT[reason],
                        quote_timestamp=timestamp,
                        quote_author=author,
                    )
                    if not result["success"]:
                        self.failed += 1
                        self.last_error = "feedback_failed"
                except Exception:
                    self.failed += 1
                    self.last_error = "feedback_failed"
            finally:
                self.feedback_queue.task_done()
                self._updated()

    async def _work(self):
        while not self.queue.empty():
            message, text, queued_at = self.queue.get_nowait()
            try:
                if monotonic() - queued_at > QUEUE_TIMEOUT:
                    self._reject(message, "expired")
                    continue
                self.active = True
                self._updated()
                await self._process(message, text)
            finally:
                self.active = False
                self.queue.task_done()
                self._updated()

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
                    signature = pipeline_signature(
                        self.coordinator.hass, self.settings["pipeline"]
                    )
                    if session and session[2] != signature:
                        # A pending HA follow-up can otherwise override the newly
                        # selected engine with the previous conversation's agent.
                        conversation_id = None
                    async with asyncio.timeout(RUN_TIMEOUT):
                        reply, conversation_id = await run_text(
                            self.coordinator.hass,
                            self.settings["pipeline"],
                            text,
                            conversation_id,
                            on_route=self.last_route.update,
                        )
                except Exception:
                    # Pipeline exceptions can contain prompts, names or provider
                    # credentials. Never log or echo their contents.
                    self.failed += 1
                    self.last_error = "pipeline_failed"
                    self._updated()
                    conversation_id = None
                    reply = "Assist could not complete this request. It was not retried; an action may already have happened."
                else:
                    self.completed += 1
                    self._updated()
                    self.sessions[key] = (conversation_id, monotonic(), signature)
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
                self.last_error = "reply_failed"
        except Exception:
            # Sending failures must not kill the worker or retry an action.
            self.failed += 1
            self.last_error = "reply_failed"

    async def stop(self):
        self.closed = True
        tasks = [
            task for task in (self.task, self.feedback_task) if task and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        while not self.feedback_queue.empty():
            self.feedback_queue.get_nowait()
            self.feedback_queue.task_done()
        self.active = False
        self.feedback_times.clear()
        self.sessions.clear()
        self.seen.clear()
        self._updated()
