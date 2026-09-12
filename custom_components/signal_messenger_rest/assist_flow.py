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

    async def async_step_assist_status(self, user_input=None):
        entry = self.config_entry
        runtime = getattr(entry, "runtime_data", None)
        saved = entry.options.get("assist", {})
        settings = runtime.assist.settings if runtime else saved
        pipeline_label, agent_label, local = describe_pipeline(
            self.hass, settings.get("pipeline")
        )
        return self.async_show_menu(
            step_id="assist_status",
            description_placeholders={
                "status": runtime.assist.status if runtime else "Not loaded",
                "pipeline": pipeline_label,
                "agent": agent_label,
                "local": local,
                "activation": (
                    "All direct text messages"
                    if settings.get("direct_mode") == "all"
                    else "Require the command prefix"
                ),
                "prefix": settings.get("prefix", "/assist"),
                "api": str(bool(runtime and runtime.last_update_success)),
                "receiver": str(bool(runtime and runtime.connected)),
                "matches": str(settings == saved),
                "sessions": str(len(runtime.assist.sessions) if runtime else 0),
                "queued": str(runtime.assist.queue.qsize() if runtime else 0),
                "error": runtime.assist.last_error if runtime else "none",
            },
            menu_options={
                "assist_status": "Refresh status",
                "assist_clear": "Clear conversation sessions",
                "assist": "Edit Assist settings",
                "init": "Back to options",
            },
        )

    async def async_step_assist_clear(self, user_input=None):
        if user_input is not None:
            runtime = getattr(self.config_entry, "runtime_data", None)
            if user_input.get("confirm") and runtime:
                runtime.assist.clear_sessions()
            return await self.async_step_assist_status()
        return self.async_show_form(
            step_id="assist_clear",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=False): BooleanSelector(),
                }
            ),
        )


def describe_pipeline(hass, pipeline_id):
    """Names are shown only in the configuration UI, never diagnostics."""
    if not pipeline_id or "assist_pipeline" not in hass.config.components:
        return "Unavailable", "Unavailable", "Unknown"
    from homeassistant.components.assist_pipeline.pipeline import (
        PipelineError,
        async_get_pipeline,
    )

    try:
        pipeline = async_get_pipeline(hass, pipeline_id)
    except KeyError, ValueError, PipelineError:
        return "Unavailable", "Unavailable", "Unknown"
    agent = pipeline.conversation_engine or "conversation.home_assistant"
    state = hass.states.get(agent)
    return (
        pipeline.name,
        state.name if state else agent,
        str(pipeline.prefer_local_intents),
    )
