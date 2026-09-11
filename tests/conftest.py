"""Fixtures for a real Home Assistant test instance."""

from unittest.mock import AsyncMock, patch

import pytest

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
