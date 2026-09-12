"""Ephemeral, user-driven QR linking shared by setup and options."""

from time import monotonic

import voluptuous as vol
from homeassistant.helpers.selector import (
    QrCodeSelector,
    QrCodeSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .api import SignalError

LINK_DISPLAY_SECONDS = 300


class LinkFlowMixin:
    """Keep link credentials in the active flow only; never in entry data."""

    _link_uri: str | None = None
    _link_deadline: float = 0

    async def async_step_link_start(self, user_input=None):
        errors = {}
        if user_input is not None:
            name = user_input["device_name"].strip()
            if not name or len(name) > 64:
                errors["base"] = "invalid_device_name"
            else:
                try:
                    self._link_before = set(await self.link_client.accounts())
                    self._link_uri = await self.link_client.start_link(name)
                except SignalError:
                    self._link_uri = None
                    # No submit-to-retry loop: the backend may have started linking.
                    return self.async_abort(reason="link_failed")
                self._link_deadline = monotonic() + LINK_DISPLAY_SECONDS
                return await self.async_step_link_scan()
        return self.async_show_form(
            step_id="link_start",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "device_name", default="Home Assistant"
                    ): TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_link_scan(self, user_input=None):
        errors = {}
        if user_input is not None:
            action = user_input["action"]
            if action == "restart":
                self._link_uri = None
                return await self.async_step_link_start()
            if action in ("check", "accounts"):
                try:
                    accounts = await self.link_client.accounts()
                except SignalError:
                    errors["base"] = "account_read_failed"
                else:
                    if action == "accounts" or set(accounts) - self._link_before:
                        self._link_uri = None
                        return await self.async_link_finished(accounts)
                    errors["base"] = "link_pending"
        fields = {}
        if self._link_uri and monotonic() < self._link_deadline:
            fields[vol.Optional("qr_code")] = QrCodeSelector(
                QrCodeSelectorConfig(data=self._link_uri, scale=5)
            )
        else:
            self._link_uri = None
            errors.setdefault("base", "link_expired")
        fields[vol.Required("action", default="check")] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    {
                        "value": "check",
                        "label": "I scanned the code — check for my account",
                    },
                    {"value": "restart", "label": "Request a new QR code"},
                    {"value": "accounts", "label": "Choose an existing account"},
                ]
            )
        )
        return self.async_show_form(
            step_id="link_scan", data_schema=vol.Schema(fields), errors=errors
        )
