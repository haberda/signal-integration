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
