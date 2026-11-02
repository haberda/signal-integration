"""Exercise the real Home Assistant text pipeline, without Signal or an LLM."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.setup import async_setup_component

from custom_components.signal_messenger_rest.assist import AssistError, run_text


async def test_pipeline_unavailable(hass):
    with pytest.raises(AssistError):
        await run_text(hass, "missing", "Hello")


async def test_real_intent_pipeline(hass):
    from homeassistant.components.assist_pipeline.pipeline import async_get_pipelines

    assert await async_setup_component(hass, "homeassistant", {})
    with patch(
        "homeassistant.components.ffmpeg.FFmpegManager.async_get_version",
        new_callable=AsyncMock,
        return_value=("test", 6),
    ):
        assert await async_setup_component(hass, "assist_pipeline", {})
    pipelines = async_get_pipelines(hass)
    assert pipelines
    with (
        patch(
            "homeassistant.components.assist_pipeline.pipeline.PipelineRun.speech_to_text",
            new_callable=AsyncMock,
        ) as stt,
        patch(
            "homeassistant.components.assist_pipeline.pipeline.PipelineRun.text_to_speech",
            new_callable=AsyncMock,
        ) as tts,
    ):
        reply, session_id = await run_text(
            hass, pipelines[0].id, "A synthetic sentence nobody recognizes"
        )
        stt.assert_not_awaited()
        tts.assert_not_awaited()
    assert isinstance(reply, str) and reply
    assert session_id
    reply2, same_id = await run_text(
        hass, pipelines[0].id, "Another synthetic sentence", session_id
    )
    assert reply2
    assert same_id == session_id


async def setup_agent_pipeline(hass):
    from homeassistant.components.assist_pipeline.pipeline import async_get_pipelines
    from homeassistant.components.conversation import (
        AbstractConversationAgent,
        ConversationResult,
        async_set_agent,
    )
    from homeassistant.helpers.intent import IntentResponse
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    class Agent(AbstractConversationAgent):
        def __init__(self, label, followup):
            self.label = label
            self.followup = followup
            self.inputs = []

        @property
        def supported_languages(self):
            return ["en"]

        async def async_process(self, user_input):
            self.inputs.append(user_input)
            response = IntentResponse(language="en")
            response.async_set_speech(self.label)
            return ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
                continue_conversation=self.followup,
            )

    assert await async_setup_component(hass, "homeassistant", {})
    with patch(
        "homeassistant.components.ffmpeg.FFmpegManager.async_get_version",
        new_callable=AsyncMock,
        return_value=("test", 6),
    ):
        assert await async_setup_component(hass, "assist_pipeline", {})
    agents = []
    for label, followup in [("Previous agent", True), ("Selected agent", False)]:
        entry = MockConfigEntry(domain="test_agent", title=label)
        entry.add_to_hass(hass)
        agent = Agent(label, followup)
        async_set_agent(hass, entry, agent)
        agents.append((entry.entry_id, agent))
    return async_get_pipelines(hass)[0], agents


async def test_local_disabled_never_intercepts_selected_agent(hass):
    from homeassistant.components.assist_pipeline.pipeline import async_update_pipeline

    pipeline, agents = await setup_agent_pipeline(hass)
    selected_id, selected = agents[1]
    await async_update_pipeline(
        hass,
        pipeline,
        conversation_engine=selected_id,
        prefer_local_intents=False,
    )
    with patch(
        "homeassistant.components.conversation.async_handle_sentence_triggers",
        new_callable=AsyncMock,
        return_value="Local automation response",
    ) as trigger:
        conversation_id = None
        routes = []
        for _ in range(3):
            reply, conversation_id = await run_text(
                hass,
                pipeline.id,
                "A conversation request",
                conversation_id,
                on_route=routes.append,
            )
            assert reply == "Selected agent"
        trigger.assert_not_awaited()
        assert (
            routes
            == [
                {
                    "selected_agent_is_local": False,
                    "prefer_local_intents": False,
                    "processed_locally": False,
                }
            ]
            * 3
        )
    assert len(selected.inputs) == 3


async def test_switching_pipeline_agent_discards_old_followup(hass, entry, api_mock):
    from dataclasses import replace

    from homeassistant.components.assist_pipeline.pipeline import async_update_pipeline

    from .test_assist import ASSIST, MESSAGE

    pipeline, agents = await setup_agent_pipeline(hass)
    old_id, old_agent = agents[0]
    new_id, new_agent = agents[1]
    await async_update_pipeline(
        hass,
        pipeline,
        conversation_engine=old_id,
        prefer_local_intents=False,
    )
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "assist": {**ASSIST, "pipeline": pipeline.id},
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    runtime.send_message = AsyncMock(return_value={"success": True})
    try:
        runtime.assist.handle(MESSAGE)
        await runtime.assist.queue.join()
        assert runtime.send_message.call_args.args[1] == "Previous agent"
        await async_update_pipeline(
            hass,
            pipeline,
            conversation_engine=new_id,
            prefer_local_intents=False,
        )
        for i in range(3):
            runtime.assist.handle(replace(MESSAGE, timestamp=1001 + i))
            await runtime.assist.queue.join()
            assert runtime.send_message.call_args.args[1] == "Selected agent"
        assert len(old_agent.inputs) == 1
        assert len(new_agent.inputs) == 3
        assert len({item.conversation_id for item in new_agent.inputs}) == 1
        assert (
            old_agent.inputs[0].conversation_id != new_agent.inputs[0].conversation_id
        )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_local_enabled_still_allows_sentence_triggers(hass):
    from homeassistant.components.assist_pipeline.pipeline import async_update_pipeline

    pipeline, agents = await setup_agent_pipeline(hass)
    selected_id, selected = agents[1]
    await async_update_pipeline(
        hass,
        pipeline,
        conversation_engine=selected_id,
        prefer_local_intents=True,
    )
    with patch(
        "homeassistant.components.conversation.async_handle_sentence_triggers",
        new_callable=AsyncMock,
        return_value="Local automation response",
    ) as trigger:
        reply, _ = await run_text(hass, pipeline.id, "Local command")
        assert reply == "Local automation response"
        trigger.assert_awaited_once()
    assert not selected.inputs
