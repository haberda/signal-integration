"""Guided onboarding, ephemeral QR codes and confirmed device operations."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.signal_messenger_rest.api import CannotConnect
from custom_components.signal_messenger_rest.const import DOMAIN

from .test_config_flow import CONNECTION, SETTINGS
from .test_onboarding_api import URI

API = "custom_components.signal_messenger_rest.api.SignalClient"
ACCOUNT = "+12025550100"
DEVICES = [
    {"id": 1, "name": "Primary"},
    {
        "id": 2,
        "name": "Desktop",
        "creation_timestamp": 1000,
        "last_seen_timestamp": 2000,
    },
]


async def submit(hass, result, data):
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def option(hass, result, data):
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


async def begin_link(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=CONNECTION
    )
    assert result["step_id"] == "account"
    return await submit(hass, result, {"account": "link_phone"})


async def test_link_new_account_and_keep_secrets_out_of_entry(hass, api_mock):
    api_mock[0].return_value = ({"mode": "json-rpc"}, [])
    api_mock[1].return_value = {}
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[]) as accounts,
        patch(f"{API}.start_link", new_callable=AsyncMock, return_value=URI) as link,
        patch(
            "custom_components.signal_messenger_rest.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await begin_link(hass)
        link.assert_not_awaited()
        result = await submit(hass, result, {"device_name": " My HA "})
        assert result["step_id"] == "link_scan"
        selector = next(
            value
            for key, value in result["data_schema"].schema.items()
            if str(key) == "qr_code"
        )
        assert selector.config["data"] == URI
        result = await submit(hass, result, None)
        assert result["step_id"] == "link_scan"
        result = await submit(hass, result, {"action": "check"})
        assert result["errors"]["base"] == "link_pending"
        link.assert_awaited_once_with("My HA")
        accounts.return_value = [ACCOUNT]
        result = await submit(hass, result, {"action": "check"})
        assert result["step_id"] == "account"
        result = await submit(hass, result, {"account": ACCOUNT})
        selector = next(
            value
            for key, value in result["data_schema"].schema.items()
            if str(key) == "destinations"
        )
        assert {"value": ACCOUNT, "label": "Note to self"} in selector.config["options"]
        result = await submit(hass, result, {**SETTINGS, "destinations": [ACCOUNT]})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {**CONNECTION, "account": ACCOUNT}
        assert URI not in repr(result["options"])
        await hass.async_block_till_done()


async def test_qr_expiry_requires_explicit_regeneration(hass, api_mock):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT]),
        patch(f"{API}.start_link", new_callable=AsyncMock, return_value=URI) as link,
        patch(
            "custom_components.signal_messenger_rest.link_flow.monotonic",
            return_value=100,
        ) as now,
    ):
        result = await begin_link(hass)
        result = await submit(hass, result, {"device_name": "HA"})
        now.return_value = 401
        result = await submit(hass, result, None)
        assert result["errors"]["base"] == "link_expired"
        assert "qr_code" not in {str(key) for key in result["data_schema"].schema}
        result = await submit(hass, result, {"action": "restart"})
        assert result["step_id"] == "link_start"
        assert link.await_count == 1
        result = await submit(hass, result, {"device_name": "HA"})
        assert result["step_id"] == "link_scan"
        assert link.await_count == 2
        result = await submit(hass, result, {"action": "accounts"})
        assert result["step_id"] == "account"


async def test_link_failures_and_name_validation(hass, api_mock):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[]),
        patch(
            f"{API}.start_link", new_callable=AsyncMock, side_effect=CannotConnect
        ) as link,
    ):
        result = await begin_link(hass)
        result = await submit(hass, result, {"device_name": " "})
        assert result["errors"]["base"] == "invalid_device_name"
        link.assert_not_awaited()
        result = await submit(hass, result, {"device_name": "HA"})
        assert result["reason"] == "link_failed"
        assert link.await_count == 1


async def test_refresh_accounts_and_transient_link_check_failure(hass, api_mock):
    with (
        patch(
            f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT, "+456"]
        ) as accounts,
        patch(f"{API}.start_link", new_callable=AsyncMock, return_value=URI),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=CONNECTION
        )
        result = await submit(hass, result, {"account": "refresh_accounts"})
        selector = next(iter(result["data_schema"].schema.values()))
        assert "+456" in {o["value"] for o in selector.config["options"]}
        result = await submit(hass, result, {"account": "link_phone"})
        result = await submit(hass, result, {"device_name": "HA"})
        accounts.side_effect = CannotConnect
        result = await submit(hass, result, {"action": "check"})
        assert result["errors"]["base"] == "account_read_failed"
        assert "qr_code" in {str(key) for key in result["data_schema"].schema}


async def open_accounts(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await option(hass, result, {"next_step_id": "accounts"})


async def test_options_link_keeps_bound_account(hass, entry):
    previous = dict(entry.options)
    with (
        patch(
            f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT]
        ) as accounts,
        patch(f"{API}.start_link", new_callable=AsyncMock, return_value=URI),
    ):
        result = await open_accounts(hass, entry)
        result = await option(hass, result, {"next_step_id": "link_start"})
        result = await option(hass, result, {"device_name": "Second account"})
        accounts.return_value = [ACCOUNT, "+456"]
        result = await option(hass, result, {"action": "check"})
        assert result["step_id"] == "accounts"
        assert "+456" in result["description_placeholders"]["accounts"]
        assert entry.data["account"] == ACCOUNT
        assert entry.options == previous


@pytest.mark.parametrize("confirm", [True, False])
async def test_device_removal_confirmation(hass, entry, confirm):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT]),
        patch(f"{API}.devices", new_callable=AsyncMock, return_value=DEVICES),
        patch(f"{API}.remove_device", new_callable=AsyncMock) as remove,
    ):
        result = await open_accounts(hass, entry)
        result = await option(hass, result, {"next_step_id": "devices"})
        assert (
            "1970-01-01T00:00:01+00:00" in result["description_placeholders"]["devices"]
        )
        result = await option(hass, result, {"next_step_id": "device_remove"})
        selector = next(iter(result["data_schema"].schema.values()))
        assert [d["value"] for d in selector.config["options"]] == ["2"]
        result = await option(hass, result, {"device": "2"})
        assert result["step_id"] == "device_confirm"
        remove.assert_not_awaited()
        result = await option(hass, result, {"confirm": confirm})
        if confirm:
            assert result["reason"] == "device_saved"
            remove.assert_awaited_once_with(ACCOUNT, 2)
        else:
            assert result["step_id"] == "accounts"
            remove.assert_not_awaited()


@pytest.mark.parametrize("failure", [False, True])
async def test_device_add_and_uncertain_failure(hass, entry, failure):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT]),
        patch(f"{API}.devices", new_callable=AsyncMock, return_value=DEVICES),
        patch(
            f"{API}.add_device",
            new_callable=AsyncMock,
            side_effect=CannotConnect if failure else None,
        ) as add,
    ):
        result = await open_accounts(hass, entry)
        result = await option(hass, result, {"next_step_id": "devices"})
        result = await option(hass, result, {"next_step_id": "device_add"})
        result = await option(hass, result, {"uri": "invalid"})
        assert result["errors"]["base"] == "invalid_link_uri"
        result = await option(hass, result, {"uri": URI})
        assert URI not in repr(result["description_placeholders"])
        add.assert_not_awaited()
        result = await option(hass, result, {"confirm": True})
        assert result["reason"] == (
            "device_change_failed" if failure else "device_saved"
        )
        add.assert_awaited_once_with(ACCOUNT, URI)
        assert URI not in repr(entry.options)


async def test_device_list_failure(hass, entry):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT]),
        patch(f"{API}.devices", new_callable=AsyncMock, side_effect=CannotConnect),
    ):
        result = await open_accounts(hass, entry)
        result = await option(hass, result, {"next_step_id": "devices"})
        assert result["reason"] == "device_read_failed"


async def test_cancel_link_does_not_restart_or_save(hass, api_mock):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[]),
        patch(f"{API}.start_link", new_callable=AsyncMock, return_value=URI) as link,
    ):
        result = await begin_link(hass)
        result = await submit(hass, result, {"device_name": "HA"})
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()
        assert hass.config_entries.async_entries(DOMAIN) == []
        link.assert_awaited_once()


async def test_account_overview_failure(hass, entry):
    with patch(f"{API}.accounts", new_callable=AsyncMock, side_effect=CannotConnect):
        result = await open_accounts(hass, entry)
        assert result["reason"] == "account_read_failed"


async def test_no_removable_devices(hass, entry):
    with (
        patch(f"{API}.accounts", new_callable=AsyncMock, return_value=[ACCOUNT]),
        patch(f"{API}.devices", new_callable=AsyncMock, return_value=DEVICES[:1]),
    ):
        result = await open_accounts(hass, entry)
        result = await option(hass, result, {"next_step_id": "devices"})
        result = await option(hass, result, {"next_step_id": "device_remove"})
        assert result["reason"] == "no_linked_devices"
