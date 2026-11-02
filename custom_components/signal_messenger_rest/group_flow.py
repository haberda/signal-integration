"""Options steps for deliberate, confirmed Signal group changes."""

from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .api import SignalError
from .const import CONF_ACCOUNT, CONF_DESTINATIONS

ACTIONS = [
    "edit",
    "add_members",
    "remove_members",
    "add_admins",
    "remove_admins",
    "leave",
]
PERMISSIONS = ["edit_group", "add_members", "send_messages"]


def select(options, *, multiple=False, custom=False):
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=multiple, custom_value=custom)
    )


def identifiers(values):
    """Trim and deduplicate user-entered numbers or UUIDs."""
    result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not result:
        raise ValueError("Select at least one person")
    return result


def describe(values):
    """Render group details and the proposed change as readable UI text."""
    labels = {
        "name": "Name",
        "description": "Description",
        "id": "Group ID",
        "members": "Members",
        "admins": "Administrators",
        "pending_invites": "Pending invitations",
        "pending_requests": "Pending join requests",
        "invite_link": "Invite link",
        "expiration_time": "Disappearing message timer (seconds)",
        "group_link": "Invite link state",
        "edit_group": "Who can edit the group",
        "add_members": "Who can add members",
        "send_messages": "Who can send messages",
    }
    lines = []
    for key, value in values.items():
        if key == "permissions":
            lines.append(describe(value))
        elif key in labels:
            if isinstance(value, list):
                value = ", ".join(
                    person
                    if isinstance(person, str)
                    else person.get("number") or person.get("uuid", "")
                    for person in value
                )
            lines.append(
                f"{labels[key]}: {value if value not in (None, '') else '(empty)'}"
            )
    return "\n\n".join(lines)


class GroupFlowMixin:
    """Group mutations apply to Signal only; receiving ACLs remain explicit."""

    async def async_step_groups(self, user_input=None):
        return self.async_show_menu(
            step_id="groups",
            menu_options={
                "group_create": "Create a group",
                "group_select": "Manage an existing group",
                "settings": "Destinations and receiving",
            },
        )

    async def async_step_group_create(self, user_input=None):
        self._group_action = "create"
        self._group = {}
        return await self._group_fields("group_create", user_input)

    async def async_step_group_select(self, user_input=None):
        errors = {}
        try:
            groups = await self.group_client.groups(
                self.config_entry.data[CONF_ACCOUNT]
            )
        except SignalError:
            groups = []
            errors["base"] = "group_read_failed"
        if user_input is not None and not errors:
            self._group = next(
                (group for group in groups if group["id"] == user_input["group"]), None
            )
            if self._group is not None:
                return await self.async_step_group_action()
            errors["base"] = "group_missing"
        if not groups and not errors:
            errors["base"] = "no_groups"
        return self.async_show_form(
            step_id="group_select",
            data_schema=vol.Schema(
                {
                    vol.Required("group"): select(
                        [
                            {
                                "value": group["id"],
                                "label": f"{group.get('name', '')} ({group['id']})",
                            }
                            for group in groups
                        ]
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_group_action(self, user_input=None):
        if user_input is not None:
            self._group_action = user_input["action"]
            return await self.async_step_group_edit()
        return self.async_show_form(
            step_id="group_action",
            description_placeholders={
                "group": self._group.get("name", self._group["id"]),
                "details": describe(self._group),
            },
            data_schema=vol.Schema(
                {
                    vol.Required("action"): select(
                        [
                            {"value": action, "label": label}
                            for action, label in zip(
                                ACTIONS,
                                [
                                    "Edit group settings",
                                    "Add members",
                                    "Remove members",
                                    "Promote administrators",
                                    "Demote administrators",
                                    "Leave group",
                                ],
                                strict=True,
                            )
                        ]
                    )
                }
            ),
        )

    async def async_step_group_edit(self, user_input=None):
        return await self._group_fields("group_edit", user_input)

    async def _group_fields(self, step, user_input):
        action = self._group_action
        errors = {}
        if user_input is not None:
            try:
                payload = dict(user_input)
                self._add_notifier = payload.pop("add_notifier", False)
                if action in ("create", "edit"):
                    if not payload["name"].strip():
                        raise ValueError("Name required")
                    payload["name"] = payload["name"].strip()
                    permissions = {
                        key: payload.pop(key)
                        for key in PERMISSIONS
                        if payload.get(key, "unchanged") != "unchanged"
                    }
                    for key in PERMISSIONS:
                        payload.pop(key, None)
                    if permissions:
                        payload["permissions"] = permissions
                    if payload.get("group_link") == "unchanged":
                        payload.pop("group_link")
                if "members" in payload:
                    payload["members"] = identifiers(payload["members"])
                if "admins" in payload:
                    payload["admins"] = identifiers(payload["admins"])
                self._group_payload = payload
                return await self.async_step_group_confirm()
            except ValueError:
                errors["base"] = "invalid_group_input"
        fields = {}
        if action == "create":
            fields[vol.Optional("add_notifier", default=False)] = BooleanSelector()
        if action in ("create", "edit"):
            fields[vol.Required("name", default=self._group.get("name", ""))] = (
                TextSelector()
            )
            fields[
                vol.Optional("description", default=self._group.get("description", ""))
            ] = TextSelector()
            # The list API does not expose the current timer or link state. Omit
            # unknown values rather than silently disabling existing settings.
            fields[vol.Optional("expiration_time")] = vol.All(
                vol.Coerce(int), vol.Range(min=0, max=2147483647)
            )
            fields[vol.Optional("group_link", default="unchanged")] = select(
                ["unchanged", "disabled", "enabled", "enabled-with-approval"]
            )
            for key in PERMISSIONS:
                fields[vol.Optional(key, default="unchanged")] = select(
                    ["unchanged", "every-member", "only-admins"]
                )
        if (
            action == "create"
            or action.endswith("members")
            or action.endswith("admins")
        ):
            key = "admins" if action.endswith("admins") else "members"
            existing = self._group.get(
                "admins" if action == "remove_admins" else "members", []
            )
            people = [
                value
                if isinstance(value, str)
                else value.get("number") or value.get("uuid")
                for value in existing
            ]
            fields[vol.Required(key)] = select(
                list(dict.fromkeys(person for person in people if person)),
                multiple=True,
                custom=True,
            )
        if action == "leave":
            self._group_payload = {}
            return await self.async_step_group_confirm()
        return self.async_show_form(
            step_id=step, data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_group_confirm(self, user_input=None):
        if user_input is not None:
            if not user_input["confirm"]:
                return await self.async_step_groups()
            try:
                created_id = await self.group_client.change_group(
                    self.config_entry.data[CONF_ACCOUNT],
                    self._group_action,
                    self._group.get("id"),
                    self._group_payload,
                )
            except SignalError:
                # End the flow: a timeout can mean the operation was applied.
                # Never present a submit button that silently repeats a mutation.
                return self.async_abort(reason="group_change_failed")
            if self._group_action == "create" and self._add_notifier:
                options = dict(self.config_entry.options)
                destinations = list(options.get(CONF_DESTINATIONS, []))
                if not any(d["recipient"] == created_id for d in destinations):
                    destinations.append(
                        {
                            "id": uuid4().hex,
                            "recipient": created_id,
                            "name": self._group_payload["name"],
                        }
                    )
                options[CONF_DESTINATIONS] = destinations
                return self.async_create_entry(data=options)
            return self.async_abort(reason="group_saved")
        return self.async_show_form(
            step_id="group_confirm",
            description_placeholders={
                "action": self._group_action.replace("_", " "),
                "group": self._group.get("name") or self._group_payload.get("name", ""),
                "details": describe(self._group_payload)
                + (
                    "\n\nAdd as notification destination: Yes"
                    if self._group_action == "create" and self._add_notifier
                    else ""
                ),
            },
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
        )
