"""Validate installable metadata, services and the blueprint against HA schemas."""

import json
from pathlib import Path

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
    assert manifest["version"] == "0.5.4"
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
