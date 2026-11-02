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
    assert entry.options["assist"] == {
        **ASSIST,
        "typing_indicator": typing,
        "quote_context": False,
    }


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


async def test_troubleshooting_unloaded_and_cancel_clear(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "assist_status"}
    )
    assert result["description_placeholders"]["status"] == "Not loaded"
    assert result["description_placeholders"]["agent"] == "Unavailable"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "assist_clear"}
    )
    assert result["step_id"] == "assist_clear"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["step_id"] == "assist_status"


async def test_troubleshooting_clear_loaded_sessions(hass, entry, api_mock):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.assist.sessions["test"] = ("private-session", 1, None)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "assist_status"}
    )
    assert result["description_placeholders"]["sessions"] == "1"
    assert "private-session" not in str(result["description_placeholders"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "assist_clear"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["description_placeholders"]["sessions"] == "0"
    assert entry.runtime_data.assist.session_generation == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_destination_pipeline_add_remove_and_preserve(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "assist": {
                **ASSIST,
                "destination_pipelines": {"group.test": "group-pipeline"},
            },
        },
    )
    with patch(
        CHOICES, return_value={"test-pipeline": "Home", "group-pipeline": "Group"}
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "assist_routes"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"destination": "alice", "pipeline": "test-pipeline"}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options["assist"]["destination_pipelines"] == {
            "group.test": "group-pipeline",
            "alice": "test-pipeline",
        }
        # Editing the general settings preserves mappings not present in its form.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "receive": True}
        )
        result = await open_assist(hass, entry)
        await hass.config_entries.options.async_configure(result["flow_id"], ASSIST)
        assert (
            entry.options["assist"]["destination_pipelines"]["alice"] == "test-pipeline"
        )
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "assist_routes"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"destination": "alice", "pipeline": ""}
        )
        assert entry.options["assist"]["destination_pipelines"] == {
            "group.test": "group-pipeline"
        }


async def test_destination_pipeline_validation(hass, entry, api_mock):
    with patch(CHOICES, return_value={}):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "assist_routes"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"destination": "   ", "pipeline": ""}
        )
        assert result["errors"]["base"] == "assist_destination_required"


async def test_quote_context_opt_in_persists(hass, entry, api_mock):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "receive": True}
    )
    with patch(CHOICES, return_value={"test-pipeline": "Home"}):
        result = await open_assist(hass, entry)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**ASSIST, "quote_context": True}
        )
        assert entry.options["assist"]["quote_context"] is True
        result = await open_assist(hass, entry)
        schema = result["data_schema"]
        assert schema({**ASSIST})["quote_context"] is True
