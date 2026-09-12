"""UI setup, account validation, destinations, and incoming permissions."""

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .addon import running_addons
from .api import (
    CannotConnect,
    InvalidAuth,
    SignalClient,
    SignalError,
    UnsupportedServer,
    normalize_url,
)
from .assist_flow import AssistFlowMixin
from .const import (
    CONF_ACCOUNT,
    CONF_DESTINATIONS,
    CONF_GROUPS,
    CONF_INTERVAL,
    CONF_RECEIVE,
    CONF_SENDERS,
    DEFAULT_INTERVAL,
    DOMAIN,
)
from .device_flow import DeviceFlowMixin
from .group_flow import GroupFlowMixin
from .link_flow import LinkFlowMixin


def connection_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(),
            vol.Optional(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): TextSelector(),
            vol.Optional(
                CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Required(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
            ): BooleanSelector(),
        }
    )


def permission_selector(choices: dict[str, str], *, groups: bool) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                {"value": key, "label": label}
                for key, label in choices.items()
                if key.startswith("group.") == groups
            ],
            multiple=True,
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


async def test_destination(client: SignalClient, account: str, recipient: str) -> None:
    result = await client.send(
        account, [recipient], "Home Assistant Signal integration test."
    )
    if result.get("errors"):
        raise SignalError("Test send failed")


def options_schema(choices: dict[str, str], defaults: dict) -> vol.Schema:
    selected = [d["recipient"] for d in defaults.get(CONF_DESTINATIONS, [])]
    return vol.Schema(
        {
            vol.Required(CONF_DESTINATIONS, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        {"value": key, "label": value} for key, value in choices.items()
                    ],
                    multiple=True,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_RECEIVE, default=defaults.get(CONF_RECEIVE, False)
            ): BooleanSelector(),
            vol.Required(
                CONF_SENDERS, default=defaults.get(CONF_SENDERS, [])
            ): permission_selector(choices, groups=False),
            vol.Required(
                CONF_GROUPS, default=defaults.get(CONF_GROUPS, [])
            ): permission_selector(choices, groups=True),
            vol.Optional("test_recipient", default=""): SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": "", "label": "Do not send a test message"}]
                    + [
                        {"value": key, "label": label} for key, label in choices.items()
                    ],
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=2, max=300, mode=NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            ),
        }
    )


def make_options(data: dict, choices: dict, previous: dict | None = None) -> dict:
    old = {d["recipient"]: d for d in (previous or {}).get(CONF_DESTINATIONS, [])}
    destinations = []
    for recipient in dict.fromkeys(r.strip() for r in data[CONF_DESTINATIONS]):
        if not recipient:
            raise ValueError("Empty destination")
        destinations.append(
            old.get(recipient)
            or {
                "id": uuid4().hex,
                "recipient": recipient,
                "name": choices.get(recipient, recipient),
            }
        )
    if not destinations:
        raise ValueError("Select at least one destination")
    data = {k: v for k, v in data.items() if k != "test_recipient"}
    return {
        **(previous or {}),
        **data,
        CONF_DESTINATIONS: destinations,
        CONF_SENDERS: list(
            dict.fromkeys(s.strip() for s in data[CONF_SENDERS] if s.strip())
        ),
        CONF_GROUPS: list(
            dict.fromkeys(s.strip() for s in data[CONF_GROUPS] if s.strip())
        ),
        CONF_INTERVAL: int(data[CONF_INTERVAL]),
    }


def client_for(hass, data: dict) -> SignalClient:
    return SignalClient(
        async_get_clientsession(hass),
        data[CONF_URL],
        data.get(CONF_USERNAME, ""),
        data.get(CONF_PASSWORD, ""),
        data.get(CONF_VERIFY_SSL, True),
    )


def error_key(err: Exception) -> str:
    if isinstance(err, InvalidAuth):
        return "invalid_auth"
    if isinstance(err, UnsupportedServer):
        return "unsupported_server"
    if isinstance(err, ValueError):
        return "invalid_url"
    return "cannot_connect"


class SignalConfigFlow(LinkFlowMixin, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._accounts: list[str] = []
        self._choices: dict[str, str] = {}
        self._client: SignalClient | None = None
        self._addons: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SignalOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is None:
            self._addons = running_addons(self.hass)
        if user_input is None and self._addons:
            return await self.async_step_addon()
        if user_input is not None:
            try:
                self._data = {
                    **user_input,
                    CONF_URL: normalize_url(user_input[CONF_URL]),
                }
                self._client = client_for(self.hass, self._data)
                _, self._accounts = await self._client.discover()
                return await self.async_step_account()
            except (SignalError, ValueError) as err:
                errors["base"] = error_key(err)
        return self.async_show_form(
            step_id="user",
            data_schema=connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_addon(self, user_input=None):
        if user_input is not None:
            url = user_input["addon"]
            if url == "manual":
                return self.async_show_form(
                    step_id="user", data_schema=connection_schema({})
                )
            return await self.async_step_user({CONF_URL: url, CONF_VERIFY_SSL: True})
        return self.async_show_form(
            step_id="addon",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "addon", default=next(iter(self._addons))
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": url, "label": label}
                                for url, label in self._addons.items()
                            ]
                            + [{"value": "manual", "label": "Enter a URL manually"}]
                        )
                    )
                }
            ),
        )

    @property
    def link_client(self):
        return self._client

    async def async_link_finished(self, accounts):
        self._accounts = accounts
        return await self.async_step_account()

    async def async_step_account(self, user_input=None):
        if user_input is not None:
            account = user_input[CONF_ACCOUNT]
            if account == "link_phone":
                return await self.async_step_link_start()
            if account == "refresh_accounts":
                try:
                    self._accounts = await self._client.accounts()
                except SignalError:
                    return self.async_abort(reason="account_read_failed")
                return await self.async_step_account()
            if account not in self._accounts:
                return self.async_abort(reason="account_missing")
            self._data[CONF_ACCOUNT] = account
            await self.async_set_unique_id(f"{self._data[CONF_URL]}|{account}")
            self._abort_if_unique_id_configured()
            try:
                self._choices = await self._client.destinations(account)
            except InvalidAuth:
                return self.async_abort(reason="invalid_auth")
            self._choices.setdefault(account, "Note to self")
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": account, "label": account}
                                for account in self._accounts
                            ]
                            + [
                                {
                                    "value": "link_phone",
                                    "label": "Link a phone account using a QR code",
                                },
                                {
                                    "value": "refresh_accounts",
                                    "label": "Refresh account list",
                                },
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_settings(self, user_input=None):
        errors = {}
        options = {}
        if user_input is not None:
            try:
                options = make_options(user_input, self._choices)
                if recipient := user_input.get("test_recipient", "").strip():
                    await test_destination(
                        self._client, self._data[CONF_ACCOUNT], recipient
                    )
            except SignalError:
                errors["base"] = "test_failed"
            except ValueError:
                errors["base"] = "no_destinations"
            else:
                return self.async_create_entry(
                    title=f"Signal {self._data[CONF_ACCOUNT]}",
                    data=self._data,
                    options=options,
                )
        return self.async_show_form(
            step_id="settings",
            data_schema=options_schema(self._choices, options),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        return await self._connection_update(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._connection_update(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def _connection_update(self, step: str, entry: ConfigEntry, user_input):
        errors = {}
        if user_input is not None:
            try:
                data = {
                    **entry.data,
                    **user_input,
                    CONF_URL: normalize_url(user_input[CONF_URL]),
                }
                _, accounts = await client_for(self.hass, data).discover()
                if data[CONF_ACCOUNT] not in accounts:
                    errors["base"] = "account_missing"
                else:
                    unique_id = f"{data[CONF_URL]}|{data[CONF_ACCOUNT]}"
                    if any(
                        e.entry_id != entry.entry_id and e.unique_id == unique_id
                        for e in self._async_current_entries()
                    ):
                        return self.async_abort(reason="already_configured")
                    return self.async_update_reload_and_abort(
                        entry, data_updates=data, unique_id=unique_id
                    )
            except (SignalError, ValueError) as err:
                errors["base"] = error_key(err)
        return self.async_show_form(
            step_id=step,
            data_schema=connection_schema(user_input or dict(entry.data)),
            errors=errors,
        )


class SignalOptionsFlow(
    AssistFlowMixin, LinkFlowMixin, DeviceFlowMixin, GroupFlowMixin, OptionsFlow
):
    def __init__(self):
        self._choices = {}

    @property
    def group_client(self):
        entry = self.config_entry
        runtime = getattr(entry, "runtime_data", None)
        return runtime.client if runtime else client_for(self.hass, entry.data)

    @property
    def link_client(self):
        return self.group_client

    async def async_link_finished(self, accounts):
        # Linking does not change the account bound to an existing config entry.
        return await self.async_step_accounts()

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            # Explicit labels also render when the frontend has cached older
            # translations that predate the options menu.
            menu_options={
                "settings": "Destinations and receiving",
                "groups": "Manage Signal groups",
                "accounts": "Accounts and linked devices",
                "assist": "Assist conversations",
            },
        )

    async def async_step_settings(self, user_input=None):
        errors = {}
        options = dict(self.config_entry.options)
        choices = {
            d["recipient"]: d["name"]
            for d in self.config_entry.options.get(CONF_DESTINATIONS, [])
        }
        if user_input is None:
            try:
                choices.update(
                    await client_for(self.hass, self.config_entry.data).destinations(
                        self.config_entry.data[CONF_ACCOUNT]
                    )
                )
            except CannotConnect, SignalError:
                pass
        self._choices.update(choices)
        if user_input is not None:
            try:
                options = make_options(
                    user_input, self._choices, dict(self.config_entry.options)
                )
                if recipient := user_input.get("test_recipient", "").strip():
                    entry = self.config_entry
                    runtime = getattr(entry, "runtime_data", None)
                    client = (
                        runtime.client if runtime else client_for(self.hass, entry.data)
                    )
                    await test_destination(client, entry.data[CONF_ACCOUNT], recipient)
            except SignalError:
                errors["base"] = "test_failed"
            except ValueError:
                errors["base"] = "no_destinations"
            else:
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="settings",
            data_schema=options_schema(
                self._choices,
                options,
            ),
            errors=errors,
        )
