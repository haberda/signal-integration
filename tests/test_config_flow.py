"""Configuration, options, duplicate prevention and reconnection."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_messenger_rest.api import (
    CannotConnect,
    InvalidAuth,
    UnsupportedServer,
)
from custom_components.signal_messenger_rest.const import DOMAIN

CONNECTION = {
    "url": "http://signal.test:8080",
    "username": "",
    "password": "",
    "verify_ssl": True,
}
SETTINGS = {
    "destinations": ["+12025550101"],
    "receive": False,
    "allowed_senders": [],
    "allowed_groups": [],
    "poll_interval": 10,
}


async def test_setup_and_duplicate(hass, api_mock):
    with patch(
        "custom_components.signal_messenger_rest.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CONNECTION
        )
        assert result["step_id"] == "account"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account": "+12025550100"}
        )
        assert result["step_id"] == "settings"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], SETTINGS
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"]["destinations"][0]["recipient"] == "+12025550101"
        assert result["options"]["receive"] is False
        await hass.async_block_till_done()
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=CONNECTION
        )
        duplicate = await hass.config_entries.flow.async_configure(
            duplicate["flow_id"], {"account": "+12025550100"}
        )
        assert duplicate["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (CannotConnect(), "cannot_connect"),
        (InvalidAuth(), "invalid_auth"),
        (UnsupportedServer(), "unsupported_server"),
    ],
)
async def test_connection_errors(hass, api_mock, exception, error):
    api_mock[0].side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=CONNECTION
    )
    assert result["errors"] == {"base": error}


async def test_no_accounts(hass, api_mock):
    api_mock[0].return_value = ({"mode": "native"}, [])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=CONNECTION
    )
    assert result["reason"] == "no_accounts"


async def test_options_preserve_ids_and_allow_manual(hass, api_mock):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**CONNECTION, "account": "+12025550100"},
        options={
            **SETTINGS,
            "destinations": [
                {"id": "fixed-id", "name": "Alice", "recipient": "+12025550101"}
            ],
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**SETTINGS, "destinations": ["+12025550101", "group.manual"]},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["destinations"][0]["id"] == "fixed-id"
    assert result["data"]["destinations"][1]["recipient"] == "group.manual"


async def test_reconfigure_preserves_account(hass, api_mock):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://signal.test:8080|+12025550100",
        data={**CONNECTION, "account": "+12025550100"},
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**CONNECTION, "url": "http://new.test/prefix/"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["url"] == "http://new.test/prefix"
    assert entry.data["account"] == "+12025550100"
    assert entry.unique_id == "http://new.test/prefix|+12025550100"
