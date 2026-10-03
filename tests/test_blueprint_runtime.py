"""Run blueprint triggers, blocking checks and cancellation in Home Assistant."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml

from custom_components.signal_messenger_rest.services import SEND_SCHEMA

ROOT = (
    Path(__file__).resolve().parents[1] / "blueprints/automation/signal_messenger_rest"
)


async def setup_blueprint(hass, filename, inputs):
    blueprint = Blueprint(
        load_yaml(str(ROOT / filename)),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    instance = BlueprintInputs(
        blueprint, {"use_blueprint": {"path": filename, "input": inputs}}
    )
    instance.validate()
    config = instance.async_substitute()
    config["alias"] = "Blueprint runtime"
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()


@pytest.mark.parametrize("failure", ["snapshot", "send_message", "record", None])
async def test_camera_failure_isolation_and_cooldown(hass, failure):
    calls = []

    async def handler(call):
        calls.append(call.service)
        if call.service == failure and (
            failure != "send_message" or calls.count("send_message") == 1
        ):
            raise HomeAssistantError("Synthetic failure")

    for domain, name in [
        ("camera", "snapshot"),
        ("camera", "record"),
        ("signal_messenger_rest", "send_message"),
    ]:
        hass.services.async_register(domain, name, handler)
    hass.states.async_set("binary_sensor.motion", "off")
    await setup_blueprint(
        hass,
        "send-camera-snapshot-to-signal-on-motion.yaml",
        {
            "account": "test",
            "recipients": ["group.test"],
            "camera": "camera.test",
            "motion_sensor": "binary_sensor.motion",
            "capture_mode": "both",
            "cooldown": 1,
        },
    )
    hass.states.async_set("binary_sensor.motion", "on")
    await asyncio.sleep(0.03)
    expected = ["snapshot"]
    if failure != "snapshot":
        expected.append("send_message")
    expected.append("record")
    if failure != "record":
        expected.append("send_message")
    assert calls == expected
    # Another motion during cooldown cannot start a second run, even after errors.
    hass.states.async_set("binary_sensor.motion", "off")
    hass.states.async_set("binary_sensor.motion", "on")
    await asyncio.sleep(0.03)
    assert calls == expected
    await asyncio.sleep(1.1)
    await asyncio.sleep(0.03)
    hass.states.async_set("binary_sensor.motion", "off")
    hass.states.async_set("binary_sensor.motion", "on")
    await asyncio.sleep(0.03)
    assert calls[len(expected)] == "snapshot"
    await hass.services.async_call(
        "automation",
        "turn_off",
        {"entity_id": "automation.blueprint_runtime"},
        blocking=True,
    )


@pytest.mark.parametrize("block_at", ["delay", "snapshot", "send_message", "record"])
async def test_camera_rechecks_blockers(hass, block_at):
    calls = []

    async def handler(call):
        calls.append(call.service)
        if call.service == block_at:
            hass.states.async_set("input_boolean.privacy", "on")

    for domain, name in [
        ("camera", "snapshot"),
        ("camera", "record"),
        ("signal_messenger_rest", "send_message"),
    ]:
        hass.services.async_register(domain, name, handler)
    hass.states.async_set("binary_sensor.motion", "off")
    hass.states.async_set("input_boolean.privacy", "off")
    await setup_blueprint(
        hass,
        "send-camera-snapshot-to-signal-on-motion.yaml",
        {
            "account": "test",
            "recipients": ["group.test"],
            "camera": "camera.test",
            "motion_sensor": "binary_sensor.motion",
            "capture_mode": "both",
            "state_entity": ["input_boolean.privacy"],
            "cooldown": 0,
            "delay": 1 if block_at == "delay" else 0,
        },
    )
    hass.states.async_set("binary_sensor.motion", "on")
    await asyncio.sleep(0.03)
    if block_at == "delay":
        hass.states.async_set("input_boolean.privacy", "on")
        await asyncio.sleep(1.1)
        await asyncio.sleep(0.03)
    assert (
        calls
        == {
            "delay": [],
            "snapshot": ["snapshot"],
            "send_message": ["snapshot", "send_message"],
            "record": ["snapshot", "send_message", "record"],
        }[block_at]
    )


async def test_reply_event_account_and_exact_command(hass):
    send = AsyncMock()
    hass.services.async_register(
        "signal_messenger_rest", "send_message", send, schema=SEND_SCHEMA
    )
    await setup_blueprint(hass, "reply.yaml", {"account": "test"})
    event = {
        "config_entry_id": "test",
        "text": "/status",
        "conversation_id": "group.test",
        "timestamp": 1234,
        "sender_uuid": None,
        "sender_number": "+12025550101",
    }
    for changed in [{"config_entry_id": "other"}, {"text": "/status extra"}]:
        hass.bus.async_fire(
            "signal_messenger_rest_message_received", {**event, **changed}
        )
    await asyncio.sleep(0.03)
    send.assert_not_awaited()
    hass.bus.async_fire("signal_messenger_rest_message_received", event)
    await asyncio.sleep(0.03)
    send.assert_awaited_once()
    assert send.await_args.args[0].data["recipients"] == ["group.test"]
    assert send.await_args.args[0].data["quote_author"] == "+12025550101"
    assert send.await_args.args[0].data["quote_timestamp"] == 1234


async def test_alert_sensor_off_cancels_real_wait(hass, entry, api_mock):
    # Use the actual registered alert service, not a stub.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "receive": False}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await asyncio.sleep(0.03)
    # Set runtime options without starting a backend receiver or reloading.
    with patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "receive": True}
        )
        await asyncio.sleep(0.03)
    runtime = entry.runtime_data
    runtime.send_message = AsyncMock(
        return_value={
            "success": True,
            "results": [{"timestamp": "1234"}],
        }
    )
    hass.states.async_set("binary_sensor.alert", "off")
    await setup_blueprint(
        hass,
        "acknowledge_alert.yaml",
        {
            "account": entry.entry_id,
            "alert_entity": "binary_sensor.alert",
            "recipient": "+12025550101",
        },
    )
    hass.states.async_set("binary_sensor.alert", "on")
    await asyncio.sleep(0.03)
    runtime.send_message.assert_awaited_once()
    assert len(runtime.alert_tasks) == 1
    hass.states.async_set("binary_sensor.alert", "unavailable")
    await asyncio.sleep(0.03)
    assert len(runtime.alert_tasks) == 1
    hass.states.async_set("binary_sensor.alert", "off")
    await asyncio.sleep(0.03)
    assert not runtime.alert_tasks
    assert "signal_messenger_rest_reaction_received" not in hass.bus.async_listeners()
    await hass.config_entries.async_unload(entry.entry_id)
