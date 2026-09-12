"""Allowlisted diagnostics: no credentials, accounts, names, IDs or bodies."""


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    return {
        "api_reachable": runtime.last_update_success,
        "receiver_connected": runtime.connected,
        "mode": runtime.client.mode,
        "filtered_events": runtime.filtered_events,
        "pending_alerts": len(runtime.alert_tasks),
        "assist_enabled": runtime.assist.settings.get("enabled", False),
        "assist_running_activation": runtime.assist.settings.get(
            "direct_mode", "prefix"
        ),
        "assist_configured_activation": entry.options.get("assist", {}).get(
            "direct_mode", "prefix"
        ),
        "assist_pipeline_matches_options": runtime.assist.settings.get("pipeline")
        == entry.options.get("assist", {}).get("pipeline"),
        "assist_last_route": dict(runtime.assist.last_route),
        "assist_queued": runtime.assist.queue.qsize(),
        "assist_completed": runtime.assist.completed,
        "assist_failed": runtime.assist.failed,
        "assist_dropped": runtime.assist.dropped,
        "assist_status": runtime.assist.status,
        "assist_last_error": runtime.assist.last_error,
        "assist_rejected_reasons": dict(runtime.assist.rejected),
        "assist_feedback_suppressed": runtime.assist.feedback_suppressed,
        "receiving_enabled": entry.options.get("receive", False),
        "destination_count": len(entry.options.get("destinations", [])),
        "allowed_sender_count": len(entry.options.get("allowed_senders", [])),
        "allowed_group_count": len(entry.options.get("allowed_groups", [])),
        "reconnects": runtime.receiver.reconnects if runtime.receiver else 0,
        "ignored_envelopes": runtime.receiver.ignored if runtime.receiver else 0,
    }
