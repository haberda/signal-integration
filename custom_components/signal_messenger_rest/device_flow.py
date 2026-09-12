"""Account overview and deliberate linked-device administration."""

from datetime import UTC, datetime

import voluptuous as vol
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SignalError, validate_link_uri
from .const import CONF_ACCOUNT


def device_description(device):
    parts = [f"{device.get('name') or 'Unnamed device'} (ID {device['id']})"]
    for key, label in (
        ("creation_timestamp", "Created"),
        ("last_seen_timestamp", "Last seen"),
    ):
        value = device.get(key)
        if isinstance(value, (int, float)) and value > 0:
            try:
                parts.append(
                    f"{label}: {datetime.fromtimestamp(value / 1000, UTC).isoformat()}"
                )
            except ValueError, OverflowError, OSError:
                pass
    return " — ".join(parts)


class DeviceFlowMixin:
    async def async_step_accounts(self, user_input=None):
        try:
            accounts = await self.link_client.accounts()
        except SignalError:
            return self.async_abort(reason="account_read_failed")
        return self.async_show_menu(
            step_id="accounts",
            description_placeholders={
                "account": self.config_entry.data[CONF_ACCOUNT],
                "accounts": ", ".join(accounts) or "None",
            },
            menu_options={
                "devices": "View and manage linked devices",
                "link_start": "Link a phone account to this backend",
                "init": "Back to options",
            },
        )

    async def async_step_devices(self, user_input=None):
        try:
            devices = await self.link_client.devices(
                self.config_entry.data[CONF_ACCOUNT]
            )
        except SignalError:
            return self.async_abort(reason="device_read_failed")
        return self.async_show_menu(
            step_id="devices",
            description_placeholders={
                "account": self.config_entry.data[CONF_ACCOUNT],
                "devices": "\n\n".join(device_description(d) for d in devices)
                or "No devices found.",
            },
            menu_options={
                "device_add": "Link another device (primary account only)",
                "device_remove": "Remove a linked device",
                "accounts": "Back to accounts",
            },
        )

    async def async_step_device_add(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._device_uri = validate_link_uri(user_input["uri"])
            except ValueError:
                errors["base"] = "invalid_link_uri"
            else:
                self._device_action = "add"
                self._device_label = "New linked device"
                return await self.async_step_device_confirm()
        return self.async_show_form(
            step_id="device_add",
            data_schema=vol.Schema(
                {
                    vol.Required("uri"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_device_remove(self, user_input=None):
        try:
            devices = await self.link_client.devices(
                self.config_entry.data[CONF_ACCOUNT]
            )
        except SignalError:
            return self.async_abort(reason="device_read_failed")
        removable = {str(d["id"]): d for d in devices if d["id"] > 1}
        if not removable:
            return self.async_abort(reason="no_linked_devices")
        errors = {}
        if user_input is not None:
            if device := removable.get(user_input["device"]):
                self._device_id = device["id"]
                self._device_label = device_description(device)
                self._device_action = "remove"
                return await self.async_step_device_confirm()
            errors["base"] = "device_missing"
        return self.async_show_form(
            step_id="device_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": key, "label": device_description(device)}
                                for key, device in removable.items()
                            ]
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_device_confirm(self, user_input=None):
        if user_input is not None:
            if not user_input["confirm"]:
                self._device_uri = None
                return await self.async_step_accounts()
            try:
                account = self.config_entry.data[CONF_ACCOUNT]
                if self._device_action == "add":
                    await self.link_client.add_device(account, self._device_uri)
                else:
                    await self.link_client.remove_device(account, self._device_id)
            except SignalError:
                return self.async_abort(reason="device_change_failed")
            finally:
                self._device_uri = None
            return self.async_abort(reason="device_saved")
        return self.async_show_form(
            step_id="device_confirm",
            description_placeholders={
                "action": self._device_action,
                "device": self._device_label,
                "account": self.config_entry.data[CONF_ACCOUNT],
            },
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
        )
