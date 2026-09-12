"""UI settings for explicit Assist access and activation."""

import voluptuous as vol
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .api import SignalError


def access_selector(choices, groups):
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                {"value": key, "label": label}
                for key, label in choices.items()
                if key.startswith("group.") == groups
            ],
            multiple=True,
            custom_value=True,
        )
    )


def pipeline_choices(hass):
    if "assist_pipeline" not in hass.config.components:
        return {}
    from homeassistant.components.assist_pipeline.pipeline import async_get_pipelines

    return {p.id: p.name for p in async_get_pipelines(hass)}


class AssistFlowMixin:
    async def async_step_assist(self, user_input=None):
        try:
            pipelines = pipeline_choices(self.hass)
        except KeyError:
            pipelines = {}
        settings = dict(self.config_entry.options.get("assist", {}))
        errors = {}
        if user_input is not None:
            settings = dict(user_input)
            settings["senders"] = list(
                dict.fromkeys(s.strip() for s in settings["senders"] if s.strip())
            )
            settings["groups"] = list(
                dict.fromkeys(g.strip() for g in settings["groups"] if g.strip())
            )
            settings["prefix"] = settings["prefix"].strip()
            settings["conversation_timeout"] = int(settings["conversation_timeout"])
            if settings["enabled"] and not self.config_entry.options.get("receive"):
                errors["base"] = "assist_receive_required"
            elif settings["enabled"] and settings.get("pipeline") not in pipelines:
                errors["base"] = "assist_pipeline_required"
            elif settings["enabled"] and not settings["senders"]:
                errors["base"] = "assist_senders_required"
            elif (
                not settings["prefix"].startswith("/")
                or any(c.isspace() for c in settings["prefix"])
                or len(settings["prefix"]) > 32
            ):
                errors["base"] = "assist_invalid_prefix"
            else:
                return self.async_create_entry(
                    data={
                        **self.config_entry.options,
                        "assist": settings,
                    }
                )
        choices = {}
        try:
            choices = await self.group_client.destinations(
                self.config_entry.data["account"]
            )
        except SignalError:
            pass
        # Keep a deleted selected pipeline visible so Assist can still be disabled.
        selected = settings.get("pipeline", "")
        options = [{"value": "", "label": "Select an Assist pipeline"}] + [
            {"value": key, "label": name} for key, name in pipelines.items()
        ]
        if selected and selected not in pipelines:
            options.append(
                {
                    "value": selected,
                    "label": "Previously selected pipeline (unavailable)",
                }
            )
        return self.async_show_form(
            step_id="assist",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "enabled", default=settings.get("enabled", False)
                    ): BooleanSelector(),
                    vol.Required("pipeline", default=selected): SelectSelector(
                        SelectSelectorConfig(options=options)
                    ),
                    vol.Required(
                        "senders", default=settings.get("senders", [])
                    ): access_selector(choices, False),
                    vol.Required(
                        "groups", default=settings.get("groups", [])
                    ): access_selector(choices, True),
                    vol.Required(
                        "direct_mode", default=settings.get("direct_mode", "prefix")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": "prefix",
                                    "label": "Require the command prefix",
                                },
                                {"value": "all", "label": "All direct text messages"},
                            ]
                        )
                    ),
                    vol.Required(
                        "prefix", default=settings.get("prefix", "/assist")
                    ): TextSelector(),
                    vol.Required(
                        "conversation_timeout",
                        default=settings.get("conversation_timeout", 300),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=30,
                            max=300,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                    vol.Optional(
                        "typing_indicator",
                        default=settings.get("typing_indicator", False),
                    ): BooleanSelector(),
                    vol.Required(
                        "publish_events", default=settings.get("publish_events", False)
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )
