"""Validate installable metadata, services and the blueprint against HA schemas."""

import json
from pathlib import Path

import pytest
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    PLATFORM_SCHEMA,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.helpers import service
from homeassistant.util.yaml import load_yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/signal_messenger_rest"


def test_metadata():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == COMPONENT.name
    assert manifest["version"] == "0.6.1"
    assert hacs["homeassistant"] == "2026.9.1"
    assert json.loads((COMPONENT / "strings.json").read_text()) == json.loads(
        (COMPONENT / "translations/en.json").read_text()
    )
    assert (COMPONENT / "brand/icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    services = load_yaml(str(COMPONENT / "services.yaml"))
    service._SERVICES_SCHEMA(services)


async def test_reply_blueprint(hass):
    data = load_yaml(
        str(ROOT / "blueprints/automation/signal_messenger_rest/reply.yaml")
    )
    blueprint = Blueprint(
        data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    assert set(blueprint.inputs) == {"account", "command", "response"}

    instance = BlueprintInputs(
        blueprint,
        {"use_blueprint": {"path": "reply.yaml", "input": {"account": "test-entry"}}},
    )
    instance.validate()
    config = PLATFORM_SCHEMA(instance.async_substitute())
    assert config["triggers"][0]["event_data"]["text"] == "/status"
    assert config["actions"][0]["action"] == "signal_messenger_rest.send_message"


async def test_acknowledgment_blueprint(hass):
    data = load_yaml(
        str(ROOT / "blueprints/automation/signal_messenger_rest/acknowledge_alert.yaml")
    )
    blueprint = Blueprint(
        data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    instance = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": "acknowledge_alert.yaml",
                "input": {
                    "account": "test-entry",
                    "alert_entity": "binary_sensor.garage",
                    "recipient": "group.test",
                },
            }
        },
    )
    instance.validate()
    config = PLATFORM_SCHEMA(instance.async_substitute())
    assert config["mode"] == "restart"
    assert [t["to"] for t in config["triggers"]] == ["on", "off"]
    assert (
        config["actions"][0]["then"][0]["action"] == "signal_messenger_rest.send_alert"
    )


async def test_options_translations_loaded_by_home_assistant(hass):
    from homeassistant.helpers.translation import async_get_translations

    translations = await async_get_translations(
        hass, "en", "options", {"signal_messenger_rest"}
    )
    prefix = "component.signal_messenger_rest.options.step."
    assert translations[prefix + "init.title"] == "Signal options"
    assert (
        translations[prefix + "settings.data.destinations"]
        == "Notification destinations"
    )
    assert translations[prefix + "group_create.data.name"] == "Group name"
    assert translations[prefix + "group_confirm.data.confirm"] == "Apply this change"
    assert translations[prefix + "link_scan.data.qr_code"] == "Signal linking code"
    assert (
        translations[prefix + "device_confirm.data.confirm"]
        == "Apply this device change"
    )
    assert translations[prefix + "assist.data.pipeline"] == "Assist pipeline"


@pytest.mark.parametrize("capture_mode", ["snapshot", "recording"])
async def test_camera_blueprint_actions(hass, capture_mode):
    from homeassistant.core import Context, State
    from homeassistant.helpers.script import Script

    data = load_yaml(
        str(
            ROOT
            / "blueprints/automation/signal_messenger_rest/send-camera-snapshot-notification-on-motion.yaml"
        )
    )
    blueprint = Blueprint(
        data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    instance = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": "camera.yaml",
                "input": {
                    "motion_sensor": "binary_sensor.motion",
                    "camera": "camera.driveway",
                    "account": "signal-entry",
                    "recipients": ["group.test"],
                    "capture_mode": capture_mode,
                    "delay": 0,
                    "cooldown": 0,
                },
            }
        },
    )
    instance.validate()
    config = PLATFORM_SCHEMA(instance.async_substitute())
    calls = []

    async def capture(call):
        calls.append((call.service, dict(call.data)))

    async def send(call):
        calls.append((call.service, dict(call.data)))

    hass.services.async_register("camera", "snapshot", capture)
    hass.services.async_register("camera", "record", capture)
    hass.services.async_register("signal_messenger_rest", "send_message", send)
    script = Script(hass, config["actions"], "camera test", "test")
    await script.async_run(
        {
            "camera_entity": "camera.driveway",
            "capture_mode": capture_mode,
            "output_directory": "/tmp",
            "motion_sensor_name": "Driveway motion",
            "this": State("automation.camera_motion", "on"),
        },
        context=Context(),
    )
    assert [name for name, _ in calls] == [
        "record" if capture_mode == "recording" else "snapshot",
        "send_message",
    ]
    filename = calls[0][1]["filename"]
    assert filename.endswith(".mp4" if capture_mode == "recording" else ".jpg")
    assert calls[1][1]["attachments"] == [filename]
    assert calls[1][1]["config_entry_id"] == "signal-entry"
    assert calls[1][1]["recipients"] == ["group.test"]
    assert calls[1][1]["message"] == "Driveway motion detected movement!"
    if capture_mode == "recording":
        assert calls[0][1]["duration"] == 10
        assert calls[0][1]["lookback"] == 0
