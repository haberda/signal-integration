"""Group management contract and confirmed options flows."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.signal_messenger_rest.api import (
    CannotConnect,
    InvalidResponse,
    SignalClient,
)

GROUP = {
    "id": "group.test",
    "name": "Household",
    "description": "Home",
    "members": ["+12025550101"],
    "admins": ["+12025550100"],
    "invite_link": "https://signal.group/example",
}
API = "custom_components.signal_messenger_rest.api.SignalClient"


async def open_groups(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "groups"}
    )


async def submit(hass, result, data):
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


async def test_create_requires_confirmation(hass, entry):
    previous = dict(entry.options)
    with patch(f"{API}.change_group", new_callable=AsyncMock) as change:
        result = await open_groups(hass, entry)
        result = await submit(hass, result, {"next_step_id": "group_create"})
        result = await submit(
            hass,
            result,
            {
                "name": " New group ",
                "members": [" +12025550101 ", "+12025550101"],
                "description": "Home",
                "expiration_time": 3600,
                "group_link": "enabled-with-approval",
                "edit_group": "only-admins",
            },
        )
        assert result["step_id"] == "group_confirm"
        change.assert_not_awaited()
        result = await submit(hass, result, {"confirm": True})
        assert result["reason"] == "group_saved"
        change.assert_awaited_once_with(
            "+12025550100",
            "create",
            None,
            {
                "name": "New group",
                "members": ["+12025550101"],
                "description": "Home",
                "expiration_time": 3600,
                "group_link": "enabled-with-approval",
                "permissions": {"edit_group": "only-admins"},
            },
        )
        assert dict(entry.options) == previous


@pytest.mark.parametrize(
    ("action", "fields", "expected"),
    [
        (
            "edit",
            {"name": "Renamed", "description": ""},
            {"name": "Renamed", "description": ""},
        ),
        ("add_members", {"members": ["+12025550102"]}, {"members": ["+12025550102"]}),
        (
            "remove_members",
            {"members": ["+12025550101"]},
            {"members": ["+12025550101"]},
        ),
        ("add_admins", {"admins": ["+12025550101"]}, {"admins": ["+12025550101"]}),
        ("remove_admins", {"admins": ["+12025550100"]}, {"admins": ["+12025550100"]}),
        ("leave", None, {}),
    ],
)
async def test_manage_group(hass, entry, action, fields, expected):
    with (
        patch(f"{API}.groups", new_callable=AsyncMock, return_value=[GROUP]),
        patch(f"{API}.change_group", new_callable=AsyncMock) as change,
    ):
        result = await open_groups(hass, entry)
        result = await submit(hass, result, {"next_step_id": "group_select"})
        result = await submit(hass, result, {"group": GROUP["id"]})
        assert "Invite link" in result["description_placeholders"]["details"]
        result = await submit(hass, result, {"action": action})
        if fields is not None:
            result = await submit(hass, result, fields)
        assert result["step_id"] == "group_confirm"
        change.assert_not_awaited()
        result = await submit(hass, result, {"confirm": True})
        assert result["reason"] == "group_saved"
        change.assert_awaited_once_with("+12025550100", action, GROUP["id"], expected)


@pytest.mark.parametrize("confirm", [True, False])
async def test_cancel_and_uncertain_failure(hass, entry, confirm):
    with patch(
        f"{API}.change_group", new_callable=AsyncMock, side_effect=CannotConnect
    ) as change:
        result = await open_groups(hass, entry)
        result = await submit(hass, result, {"next_step_id": "group_create"})
        result = await submit(
            hass, result, {"name": "New", "members": ["+12025550101"]}
        )
        result = await submit(hass, result, {"confirm": confirm})
        if confirm:
            assert result["reason"] == "group_change_failed"
            assert change.await_count == 1
        else:
            assert result["step_id"] == "groups"
            change.assert_not_awaited()


async def test_group_read_failure(hass, entry):
    with patch(f"{API}.groups", new_callable=AsyncMock, side_effect=CannotConnect):
        result = await open_groups(hass, entry)
        result = await submit(hass, result, {"next_step_id": "group_select"})
        assert result["errors"]["base"] == "group_read_failed"


@pytest.mark.parametrize(
    ("action", "method", "suffix", "payload"),
    [
        ("create", "POST", "", {"name": "Test", "members": ["+123"]}),
        ("edit", "PUT", "/group.test", {"name": "Changed"}),
        ("add_members", "POST", "/group.test/members", {"members": ["+123"]}),
        ("remove_members", "DELETE", "/group.test/members", {"members": ["+123"]}),
        ("add_admins", "POST", "/group.test/admins", {"admins": ["+123"]}),
        ("remove_admins", "DELETE", "/group.test/admins", {"admins": ["+123"]}),
        ("leave", "POST", "/group.test/quit", {}),
    ],
)
async def test_api_group_routes(action, method, suffix, payload):
    client = SignalClient(None, "http://signal.test")
    with patch.object(
        client, "request", new_callable=AsyncMock, return_value={"id": "group.new"}
    ) as request:
        await client.change_group(
            "+123", action, None if action == "create" else "group.test", payload
        )
        request.assert_awaited_once_with(
            method, "/v1/groups/%2B123" + suffix, data=payload
        )


async def test_groups_validates_response():
    client = SignalClient(None, "http://signal.test")
    with patch.object(client, "request", new_callable=AsyncMock, return_value=[GROUP]):
        assert await client.groups("+123") == [GROUP]
    with patch.object(
        client, "request", new_callable=AsyncMock, return_value=[{"id": 1}]
    ):
        with pytest.raises(InvalidResponse):
            await client.groups("+123")


async def test_menu_labels_survive_missing_frontend_translations(hass, entry):
    """Menu payloads must contain labels, not only translation identifiers."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == {
        "settings": "Destinations and receiving",
        "groups": "Manage Signal groups",
    }
    result = await submit(hass, result, {"next_step_id": "groups"})
    assert result["menu_options"] == {
        "group_create": "Create a group",
        "group_select": "Manage an existing group",
        "settings": "Destinations and receiving",
    }
    result = await submit(hass, result, {"next_step_id": "group_create"})
    assert result["step_id"] == "group_create"


async def test_create_group_with_notifier(hass, entry, api_mock):
    from homeassistant.helpers import entity_registry as er

    previous = dict(entry.options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with patch(
        f"{API}.change_group", new_callable=AsyncMock, return_value="group.new"
    ) as change:
        result = await open_groups(hass, entry)
        result = await submit(hass, result, {"next_step_id": "group_create"})
        result = await submit(
            hass,
            result,
            {
                "name": "New group",
                "members": ["+12025550101"],
                "add_notifier": True,
            },
        )
        assert (
            "Add as notification destination: Yes"
            in result["description_placeholders"]["details"]
        )
        assert entry.options == previous
        result = await submit(hass, result, {"confirm": True})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert "add_notifier" not in change.call_args.args[3]
        assert entry.options["destinations"][:-1] == previous["destinations"]
        assert entry.options["allowed_groups"] == previous["allowed_groups"]
        assert entry.options["allowed_senders"] == previous["allowed_senders"]
        destination = entry.options["destinations"][-1]
        assert destination["recipient"] == "group.new"
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "notify", entry.domain, f"{entry.entry_id}_{destination['id']}"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None
        assert registry.async_get(entity_id).original_name == "New group"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_create_returns_valid_group_id():
    client = SignalClient(None, "http://signal.test")
    with patch.object(
        client, "request", new_callable=AsyncMock, return_value={"id": "group.new"}
    ):
        assert (
            await client.change_group("+123", "create", None, {"name": "New"})
            == "group.new"
        )
    with patch.object(
        client, "request", new_callable=AsyncMock, return_value={"id": ""}
    ):
        with pytest.raises(InvalidResponse):
            await client.change_group("+123", "create", None, {"name": "New"})
