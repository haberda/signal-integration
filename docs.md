# Usage guide

See the [README](README.md) for installation and initial setup.

## Connection and receiving setup

Setup detects running production and edge Signal add-ons on Home Assistant OS/Supervised and uses their internal hostname and port 8080. Other installations need a manual API URL reachable from Home Assistant. Optional username/password fields are for an HTTP Basic-auth proxy, not your Signal login.

Use **Reconfigure** to change the URL or proxy credentials while retaining the account. Changing integration options reloads the entry.

| Backend mode | Receiving |
| --- | --- |
| `json-rpc`, `json-rpc-native` | WebSocket with automatic reconnect |
| `normal`, `native` | Polling; configurable interval, default 10 seconds |

For polling, disable competing receive sensors/helpers and the backend's `AUTO_RECEIVE` or `AUTO_RECEIVE_SCHEDULE`. In send-only polling deployments, keep the backend's periodic receiving enabled.

Incoming event permissions and Assist permissions are configured separately. Both require an allowed sender, plus an allowed group for group chats. Display names do not grant permission.

## Sending attachments and replies

Use `notify.send_message` for text sent to a configured notification entity. Use the integration's rich action for files, URLs, custom recipients, formatting and quoted replies:

```yaml
action: signal_messenger_rest.send_message
data:
  recipients:
    - "+12025550101"
  message: "Person detected at the front door"
  attachments:
    - /config/snapshots/front_door.jpg
response_variable: signal_result
```

Create the directory and allow Home Assistant to access it:

```yaml
homeassistant:
  allowlist_external_dirs:
    - /config/snapshots
```

Merge this into your existing `homeassistant:` configuration and restart. URL attachments use `urls` instead of `attachments` and require `allowlist_external_urls`.

- Select `config_entry_id` in the action editor if you have multiple Signal accounts.
- Recipients can be phone numbers with country codes, UUIDs, usernames or REST `group.` IDs. Omitting `recipients` sends to all configured destinations.
- Attachments are limited to **five files, 10 MiB per file, 20 MiB combined**.
- Optional `title` is prepended to the message. Set `text_mode: styled` for supported text formatting.
- For a quoted reply, supply both `quote_timestamp` (original milliseconds) and `quote_author` (sender UUID or number). `quote_message` is optional.

The response contains `success` and per-recipient `results`, including timestamps or sanitized errors. Without `response_variable`, failed destinations raise an action error. Sends are never retried automatically: a timeout can occur after delivery.

## Assist

First configure an assistant in Home Assistant, then:

1. Enable receiving under **Configure → Destinations and receiving**.
2. Open **Configure → Assist conversations**, enable Assist, select a pipeline, and allow specific senders and groups.
3. Choose **Require the command prefix** or **All direct text messages**. Groups always require the prefix.
4. Send `/assist turn on the kitchen lights`.

Assist handles text only. Allowed senders can use the selected agent's capabilities and exposed entities; Signal identities are not mapped to individual Home Assistant users.

Context is separate per account, chat and sender, with a configurable idle timeout of 30–300 seconds. Send `/assist /reset` to start fresh, or `/reset` in all-direct-messages mode. This does not delete agent history.

Optional settings:

| Setting | Use |
| --- | --- |
| Show typing while Assist responds | Displays best-effort progress in Signal |
| Include quoted text as Assist context | Sends up to 2,000 characters of quoted text to the selected agent/provider, including a cloud provider if configured |
| Also publish handled messages as events | Allows message automations to also handle Assist requests; leave off to avoid duplicate responses |

Quotes are labeled as reference text, but interpretation depends on the agent. They do not bypass permissions or prefix requirements.

Use **Assist pipelines by destination** to override the default pipeline for a sender or group. In direct chats, UUID assignments take precedence over phone-number assignments. Select **Use default pipeline** to remove an override. Assignments do not grant access; missing pipelines fail rather than silently selecting another agent.

Use **Assist troubleshooting** to inspect the pipeline, agent, activation mode, connections and last error, or clear conversation references. The pipeline's **Prefer handling commands locally** setting controls whether Home Assistant can answer commands before the selected agent.

Requests are limited to 4,000 characters, with one active request and 16 queued requests per account. Queued requests expire after 60 seconds; active pipeline runs also have a 60-second timeout. Busy, oversized and expired requests receive rate-limited explanatory replies. Reloading clears pending work and context references.

## Receiving and automations

Authorized incoming messages emit `signal_messenger_rest_message_received`. Key event fields:

| Fields | Use |
| --- | --- |
| `config_entry_id`, `account` | Receiving integration entry/account |
| `sender_uuid`, `sender_number` | Sender identity; either may be absent |
| `conversation_id`, `conversation_kind` | Reply destination and `direct`/`group` |
| `timestamp`, `received_at` | Original milliseconds and HA receipt time |
| `text`, `attachments`, `quote` | Message text and attachment/quote metadata |

Incoming attachment files are not downloaded by this integration. Sync echoes and typing/receipt events do not trigger message automations.

Import the [reply blueprint](blueprints/automation/signal_messenger_rest/reply.yaml) to respond to an exact command, or write an automation:

```yaml
alias: Signal status reply
triggers:
  - trigger: event
    event_type: signal_messenger_rest_message_received
    event_data:
      text: /status
actions:
  - action: signal_messenger_rest.send_message
    data:
      config_entry_id: "{{ trigger.event.data.config_entry_id }}"
      recipients: "{{ [trigger.event.data.conversation_id] }}"
      message: "Home temperature: {{ states('sensor.home_temperature') }}"
mode: queued
max: 10
```

### Reactions and acknowledgment alerts

Use `signal_messenger_rest.send_reaction` with `recipient`, `target_author`, `timestamp` and `emoji`. Use `remove_reaction` with the same message reference to remove it. Select an account when multiple entries are loaded.

Incoming `signal_messenger_rest_reaction_received` events use the same permissions as messages. They include `emoji`, `target_timestamp`, `target_author`, `target_author_uuid`, `target_author_number` and `removed`, alongside sender/conversation fields.

The [acknowledgment blueprint](blueprints/automation/signal_messenger_rest/acknowledge_alert.yaml) repeats an alert until an authorized sender reacts to the original message, the sensor clears, or the wait expires. You can also call the action directly:

```yaml
action: signal_messenger_rest.send_alert
data:
  recipient: "+12025550101"
  message: "The garage door is open. React with ✅ to acknowledge."
  emoji: "✅"
  expiry: 600
  reminder_interval: 120
response_variable: alert_result
```

Receiving must be enabled. Use a phone number, UUID or REST group ID, not a username. If reactions hide the sending account's number, set `account_author` to that account's UUID. The response includes `acknowledged`, `reason` and the original `timestamp`. Reload/restart cancels pending alerts.

### Read receipts

Enable **Send read receipts** under **Destinations and receiving**. This is off by default and applies to messages accepted for events or handled by Assist, including rejected Assist requests. It acknowledges receipt, not successful command execution. Filtered messages and reactions are not acknowledged. Delivery is best-effort.

## Camera notifications

Import the [camera motion blueprint](blueprints/automation/signal_messenger_rest/send-camera-snapshot-to-signal-on-motion.yaml) and select a sensor, camera, Signal account and recipients. Choose:

- **Snapshot**: send a JPEG.
- **Video recording**: record and send an MP4.
- **Snapshot, then video (both)**: send the image first, then record and send a separate clip.

Create and allow the capture directory using `allowlist_external_dirs` as shown above. The blueprint defaults to `/tmp`; allow that path if you keep the default.

Video requires a camera supporting `camera.record` and Home Assistant's stream integration. Default duration is 10 seconds; optional lookback uses footage already buffered by an active stream. Keep clips below **10 MiB** by reducing duration or stream bitrate.

All selected blocking entities must be off; leave the list empty to disable filtering. Motion during capture, sending and cooldown is ignored. Captures remain on disk, so arrange cleanup. Older blueprint instances using `notify_service` need the new account and recipient inputs.

HACS does not install blueprints automatically. Import their GitHub URLs or copy them to your HA `blueprints/automation/signal_messenger_rest/` directory.

## Accounts, groups and devices

Under **Accounts and linked devices**, view available accounts or link a phone using a QR code. If the code expires, request a new one. If QR linking is unsupported, link through the backend and refresh the account list. SMS registration, CAPTCHA and PIN handling remain in the backend.

Each account needs a separate integration entry. Linking another account does not switch an existing entry. Device administration depends on the backend's role; if rejected, manage linked devices on your phone. Removing the HA integration does not unlink your Signal account.

Under **Manage Signal groups**, create groups, edit settings, manage members/admins, or leave a group. Changes require confirmation and appropriate Signal permissions. **Leave group** does not delete it for everyone.

Select **Add as notification destination** during creation to create a notifier, or select the group later in **Destinations and receiving**. Incoming group permissions remain separate.

## Migrating from the built-in integration

Both integrations can coexist. Configure this integration with your existing backend/account, test sending, then update automations:

| Old usage | Replacement |
| --- | --- |
| YAML connection/account/recipients | UI configuration |
| `notify.<old_name>` | `notify.send_message` targeting a destination entity |
| `target` recipient list | Rich action `recipients` |
| Nested attachment/URL/format fields | Top-level rich action fields |

Remove the old notifier YAML after migration. Stop competing receivers before enabling incoming messages.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API unreachable | URL from HA, backend status, port, proxy credentials and TLS |
| API reachable but receiving disconnected | Backend mode and WebSocket proxy support |
| No incoming events | Sender/group permissions; stop competing polling receivers |
| Assist does not respond | Receiving enabled, separate Assist permissions, prefix and selected pipeline |
| Assist responds twice | Disable publishing handled messages as events or remove overlapping automations |
| Unexpected Assist agent | Destination overrides, local-command preference and Assist troubleshooting |
| Attachment fails | File exists inside HA, path/URL is allowlisted, size is within limits |
| Camera recording fails | Camera stream support, output directory, stream integration and clip size |
| Reaction does not acknowledge an alert | React to the original message; verify emoji, permissions and author UUID |
| HTTP 400 on send | The backend rejected the request; inspect backend details before changing contacts or retrying |
| Read receipts missing | Enable the option; check receipt counters in downloaded diagnostics |

The account device exposes API/receiver health, timestamps and Assist status/counters. Download diagnostics for additional failure and rejection counters; these reset on reload and exclude message content and credentials.

A timeout does not prove an action failed. Check its outcome before repeating it. Messages may be missed during disconnection, and duplicate tracking resets on restart. Automation traces and the selected Assist provider can retain content even though integration diagnostics omit it. Keep the backend API on a private network or behind a protected proxy.
