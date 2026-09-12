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
        "assist_queued": runtime.assist.queue.qsize(),
        "assist_completed": runtime.assist.completed,
        "assist_failed": runtime.assist.failed,
        "assist_dropped": runtime.assist.dropped,
        "receiving_enabled": entry.options.get("receive", False),
        "destination_count": len(entry.options.get("destinations", [])),
        "allowed_sender_count": len(entry.options.get("allowed_senders", [])),
        "allowed_group_count": len(entry.options.get("allowed_groups", [])),
        "reconnects": runtime.receiver.reconnects if runtime.receiver else 0,
        "ignored_envelopes": runtime.receiver.ignored if runtime.receiver else 0,
    }
