"""Assist options require explicit permissions and preserve other settings."""

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from .test_assist import ASSIST
from .test_config_flow import SETTINGS

CHOICES = "custom_components.signal_messenger_rest.assist_flow.pipeline_choices"


async def open_assist(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "assist"}
    )


@pytest.mark.parametrize(
    ("receiving", "settings", "pipelines", "error"),
    [
        (False, ASSIST, {"test-pipeline": "Home"}, "assist_receive_required"),
        (True, {**ASSIST, "pipeline": ""}, {}, "assist_pipeline_required"),
        (
            True,
            {**ASSIST, "senders": []},
            {"test-pipeline": "Home"},
            "assist_senders_required",
        ),
        (
            True,
            {**ASSIST, "prefix": "wrong prefix"},
            {"test-pipeline": "Home"},
            "assist_invalid_prefix",
        ),
    ],
)
async def test_assist_validation(
    hass, entry, api_mock, receiving, settings, pipelines, error
):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "receive": receiving}
    )
    with patch(CHOICES, return_value=pipelines):
        result = await open_assist(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], settings
        )
        assert result["errors"]["base"] == error
        assert "assist" not in entry.options


@pytest.mark.parametrize("typing", [False, True])
async def test_save_assist_and_other_options_preserve_each_other(
    hass, entry, api_mock, typing
):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "receive": True}
    )
    destinations = entry.options["destinations"]
    with patch(CHOICES, return_value={"test-pipeline": "Home"}):
        result = await open_assist(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**ASSIST, "typing_indicator": typing}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options["destinations"] == destinations
        assert entry.options["allowed_senders"] == []
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    await hass.config_entries.options.async_configure(result["flow_id"], SETTINGS)
    assert entry.options["assist"] == {**ASSIST, "typing_indicator": typing}


async def test_disable_when_pipeline_is_unavailable(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "assist": ASSIST}
    )
    result = await open_assist(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ASSIST, "enabled": False}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["assist"]["enabled"] is False
