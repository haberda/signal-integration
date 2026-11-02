"""Fixtures for a real Home Assistant test instance."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_messenger_rest.const import DOMAIN

from .test_config_flow import CONNECTION, SETTINGS

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def api_mock():
    with (
        patch(
            "custom_components.signal_messenger_rest.api.SignalClient.discover",
            new_callable=AsyncMock,
        ) as discover,
        patch(
            "custom_components.signal_messenger_rest.api.SignalClient.destinations",
            new_callable=AsyncMock,
        ) as destinations,
    ):
        discover.return_value = (
            {"mode": "json-rpc", "versions": ["v1", "v2"], "version": "0.100"},
            ["+12025550100"],
        )
        destinations.return_value = {"+12025550101": "Alice", "group.test": "Household"}
        yield discover, destinations


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Signal test",
        data={**CONNECTION, "account": "+12025550100"},
        options={
            **SETTINGS,
            "destinations": [
                {"id": "alice", "recipient": "+12025550101", "name": "Alice"},
                {"id": "group", "recipient": "group.test", "name": "Household"},
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry
