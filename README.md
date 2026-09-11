# Signal Messenger REST

![Integration icon](custom_components/signal_messenger_rest/brand/icon.png)

A Home Assistant custom integration for [Signal CLI REST API](https://github.com/bbernhard/signal-cli-rest-api), including the [Signal Messenger add-on](https://github.com/haberda/signal-addon).

Configure accounts and notification destinations in the UI, send messages and attachments, and trigger automations from authorized incoming messages. This integration uses the separate domain `signal_messenger_rest`, so the built-in Signal integration can coexist during migration.

## Requirements and installation

- Home Assistant **2026.9.1 or newer**.
- A running Signal REST API with an account already registered or linked. Account registration and QR linking remain in the backend.
- An API URL reachable **from Home Assistant**. On HAOS, `localhost` refers to the HA container, not the add-on. Use its reachable hostname or host IP and port.

For HACS, add the public GitHub mirror of this repository under **HACS → Custom repositories**, select **Integration**, download **Signal Messenger REST**, and restart Home Assistant. Then open **Settings → Devices & services → Add integration → Signal Messenger REST**.

This checkout currently uses a self-hosted Git remote. HACS supports public GitHub repositories, so a public mirror must exist before that installation route works. Repository structure, metadata, local brand assets, and validation workflows are included. No GitHub repository or release is created automatically. [HACS hosting requirements](https://www.hacs.xyz/docs/faq/other_git_providers/).

For local testing before publication, copy `custom_components/signal_messenger_rest` into your HA configuration's `custom_components` directory and restart HA.

## UI setup

1. Enter the backend URL. Path prefixes are supported. Optional username/password fields are for an HTTP Basic-auth reverse proxy, not a Signal account password. TLS certificate verification defaults to on.
2. Choose a linked account.
3. Select contacts/groups or type recipient numbers, UUIDs, usernames, or REST `group.` IDs manually. At least one notification destination is required. You can rename the resulting entities in Home Assistant.
4. Enable receiving if needed, and select allowed contacts/groups by name or enter sender numbers/UUIDs manually. An empty list allows **no incoming message events**. Group messages require both an allowed sender and an allowed group ID.
5. Optionally select a test destination before submitting. This sends one fixed test message to that destination only. The default sends nothing. A failed or uncertain test leaves settings unsaved and resets the test selection; saving again does not automatically resend.

Use integration **Configure** to change destinations, receiving permissions and polling intervals. Use **Reconfigure** to change the URL or proxy credentials while retaining the same Signal account. A proxy authentication failure starts a credential recovery flow. Changing options reloads the integration.

## Sending

Each selected destination gets a modern notification entity:

```yaml
action: notify.send_message
target:
  entity_id: notify.signal_household
data:
  title: Home Assistant
  message: The washing machine has finished.
```

Entity IDs depend on your account and destination names; select the actual entity in the UI. A title is prepended to the text with a blank line.

Use the rich action for attachments, custom recipients, formatting, and replies:

```yaml
action: signal_messenger_rest.send_message
data:
  recipients:
    - "+12025550101"
  message: "**Person detected** at the front door"
  text_mode: styled
  attachments:
    - /config/snapshots/front_door.jpg
response_variable: signal_result
```

When several accounts are loaded, also select `config_entry_id` in the action editor. If `recipients` is omitted, the action sends to all destinations configured on that account. Direct and group destinations can be mixed: the integration makes one request per destination.

The response contains `success` and a `results` list. Each result contains `recipient`, `success`, and either the backend `timestamp` or a sanitized `error`. Without `response_variable`, any failed destination causes the action to raise an error. A successful REST response is not proof of delivery or reading. A timeout may mean a message was sent; the integration never automatically retries sends.

For quoted replies, set `quote_timestamp` to the original message's millisecond timestamp and `quote_author` to its sender UUID or number. `quote_message` is optional. Both timestamp and author are required together.

Attachment paths must be allowed by HA's `allowlist_external_dirs`. URLs, supplied in `urls`, must be allowed by `allowlist_external_urls`; every redirect is checked too. Backend proxy credentials are never forwarded to attachment URLs. Downloads always verify TLS. Limits are five attachments, 10 MiB per file, and 20 MiB combined; base64 encoding increases the request size. Files are read off the event loop.

```yaml
homeassistant:
  allowlist_external_dirs:
    - /config/snapshots
  allowlist_external_urls:
    - https://camera.example.net/snapshots/
```

## Receiving and automations

| Backend mode | Transport | Configuration |
| --- | --- | --- |
| `json-rpc` | WebSocket | Recommended; automatic reconnect |
| `json-rpc-native` | WebSocket | Supported by the client where available in the backend |
| `normal`, `native` | HTTP polling | Default 10 seconds between batches; configurable from 2–300 seconds |

For normal/native receiving, disable the add-on's `AUTO_RECEIVE` or upstream `AUTO_RECEIVE_SCHEDULE`, and stop old receive sensors or helper receivers using that account. Those consumers can take messages before this integration sees them. In send-only normal/native deployments, retain the backend's periodic receiving as recommended upstream. The integration does not modify add-on settings. [Upstream receiving guidance](https://github.com/bbernhard/signal-cli-rest-api#auto-receive-schedule).

Authorized text/attachment messages emit `signal_messenger_rest_message_received` with:

| Field | Meaning |
| --- | --- |
| `schema_version` | Currently `1` |
| `config_entry_id`, `account` | Receiving HA entry and Signal account |
| `sender_uuid`, `sender_number` | Sender identity; either can be absent |
| `source_device` | Sender device, when supplied |
| `timestamp` | Original message timestamp in milliseconds |
| `received_at` | HA receipt time in UTC |
| `conversation_kind` | `direct` or `group` |
| `conversation_id` | Recipient identifier ready to use for replies |
| `text` | Message body, or an empty string for an attachment-only message |
| `attachments`, `quote` | Limited attachment metadata and optional quote |

The event entity exposes only an event type and timestamp, without message bodies or sender IDs in its attributes. Use the event bus payload for content-aware automations. Sync echoes, typing notifications, receipts, and other unsupported envelopes do not trigger command automations. Reactions emit a separate event and never masquerade as text messages. Incoming attachment files are not fetched by HA. Backend JSON-RPC attachment download policy remains controlled by the backend.

An included [reply blueprint](blueprints/automation/signal_messenger_rest/reply.yaml) matches an exact authorized command and responds in the same conversation. HACS does not automatically install blueprints. Copy it into your HA configuration's `blueprints/automation/signal_messenger_rest/` directory, or import its GitHub URL after publication.

Equivalent automation:

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
      quote_timestamp: "{{ trigger.event.data.timestamp }}"
      quote_author: "{{ trigger.event.data.sender_uuid or trigger.event.data.sender_number }}"
mode: queued
max: 10
```

Only senders authorized in integration options reach this automation. Display names are never used for authorization. Incoming text is not evaluated as a template or arbitrary service name. If your automation controls a sensitive device, add the conditions appropriate for that action.

## Reactions and alert acknowledgment

Send a reaction to a specific message:

```yaml
action: signal_messenger_rest.send_reaction
data:
  recipient: "+12025550101"
  target_author: "+12025550101"
  timestamp: 1789123456789
  emoji: "✅"
```

Use `signal_messenger_rest.remove_reaction` with the same reference to remove your reaction; `emoji` is optional for removal. Select `config_entry_id` when multiple accounts are loaded. `target_author` identifies the original message author, and `timestamp` is the original message timestamp, not the reaction time. Backend errors surface as action failures; uncertain requests are not retried.

Incoming `signal_messenger_rest_reaction_received` events retain the account, sender, conversation, receive time and reaction timestamp fields described above. They add:

| Field | Meaning |
| --- | --- |
| `emoji` | Exact reaction emoji |
| `target_timestamp` | Timestamp of the original message being reacted to |
| `target_author` | Original author UUID, or number when UUID is absent |
| `target_author_uuid`, `target_author_number` | Original author identifiers, when available |
| `removed` | `true` when the reaction was removed |

These events contain no message text. Both messages and reactions use the same incoming permissions. The content-free event entity now reports either `message_received` or `reaction_received`. Distinct emoji changes and removals are not suppressed as duplicates.

The [acknowledgment blueprint](blueprints/automation/signal_messenger_rest/acknowledge_alert.yaml) sends an alert when a selected binary sensor turns on. Reminders quote the original alert. An approved person's matching reaction to the **original message** stops reminders. The sensor turning off also stops the action. A configurable expiry bounds the wait. Like the reply blueprint, copy/import it separately from the integration.

The underlying action can also be used directly:

```yaml
action: signal_messenger_rest.send_alert
data:
  recipient: "+12025550101"
  message: "The garage door is open. React to this message with ✅ to acknowledge."
  emoji: "✅"
  expiry: 600
  reminder_interval: 120
response_variable: alert_result
```

It returns `acknowledged`, `reason` (`acknowledged` or `expired`), and the original `timestamp`. Receiving must be enabled. Destination numbers, UUIDs and REST group IDs are supported; usernames are not supported for acknowledgment correlation. The reacting sender must be permitted; a group destination must also be allowed.

If reactions identify your sending account only by UUID, set `account_author` to that account's UUID. It is **not** the reacting person's UUID. Matching requires the account, conversation, original message author and timestamp, exact emoji, and a non-removal event. An unrelated reaction or removal cannot acknowledge an alert.

The listener is installed before the initial send so fast reactions can be matched after the message reference arrives. Reminders never replace the original reference. Set `reminder_interval` greater than `expiry` to disable reminders. Failed/uncertain sends stop the action. At most 16 alerts may wait per account. Reload, shutdown or automation cancellation clears pending waits and listeners; acknowledgments are not persisted across restarts. Expiry stops scheduling reminders but cannot retract an already in-flight send.

## Diagnostics and limitations

The integration exposes API reachability, receiver connectivity, last successful send, and last authorized activity time. API reachability measures the REST connection; it does not establish Signal network health. In polling mode, receiver connectivity reflects the most recent receive attempt. Quiet conversations do not imply an outage.

Duplicate suppression uses a bounded, in-memory cache. Restart/reload clears it, and live WebSocket messages may be lost while disconnected. There is no durable inbox, replay guarantee, unread count, or exactly-once command processing. Avoid using this transport as the sole path for critical alerts.

Signal messages are decrypted in the backend and then passed to HA. Diagnostic downloads omit account identifiers, connection URLs, credentials and message contents, but automation traces and event listeners can retain received content. The API endpoint is privileged: use a private network or a protected proxy. Removing the integration never unlinks or deletes the Signal account.

Receipt actions, message editing/deletion, group administration, polls, and embedded QR onboarding remain outside version 0.2.0.

## Migration from the built-in integration

Configure the same backend/account in this integration, then update automations:

| Legacy setting | New equivalent |
| --- | --- |
| YAML `url`, `number`, `recipients` | UI connection, account, destinations |
| `notify.<old_name>` | `notify.send_message` with a destination entity, or the rich send action |
| `target` recipient list | Rich action `recipients` |
| Nested `data.attachments`, `data.urls`, `data.text_mode` | Top-level rich action fields |
| Per-send `verify_ssl` for attachment URLs | Not carried over; attachment HTTPS verification is required |

After verifying sending, remove the old YAML notifier. Before enabling receiving, remove competing receive consumers. The integration does not edit YAML or automations and does not claim legacy action aliases.

## Validation status

| Environment | Status |
| --- | --- |
| HA 2026.9.1 / Python 3.14 | Automated setup, actions, entities and lifecycle tests |
| Local mock HTTP and WebSocket servers | Automated protocol, auth, reconnect and error tests |
| REST API 0.100 | Source contract reviewed; no linked live account tested |
| Signal Messenger add-on | No live image tested; record version/digest during acceptance |
| HACS installation / GitHub validation | Prepared; requires a public GitHub mirror |

Before a public release, validate these cases on a linked test account: direct/group/self sending; a local and URL attachment; a quoted reply to direct/group messages; two identical incoming texts; a sender without a phone number; unauthorized sender/group filtering; backend restart; HA restart/reload; a backend mode change; account unlinking; send timeout and partial failure. Check add-on and standalone deployments independently. Never send real messages from automated CI.

### Receiving troubleshooting

| Symptom | Check |
| --- | --- |
| API unreachable | URL as seen from HA, add-on/container status, port, TLS and proxy credentials |
| API reachable, receiver disconnected | Backend mode and WebSocket proxy support; reconfigure credentials if requested |
| Connected but no events | Incoming sender/group selections and the `filtered_events` diagnostic count; group events require both permissions |
| Some polling messages missing | Disable competing receive sensors, helper receivers and backend automatic receive schedules |
| Text arrives but reaction does not acknowledge | React to the original alert, check exact emoji/author/conversation, and supply `account_author` if the account number is hidden |
| Reminders stop after restart/reload | Expected: pending alerts are in memory and canceled on reload |

The diagnostic `pending_alerts` count reports current waits without message contents or identities. Reaction fixtures were derived from the [signal-cli v0.14.5 JSON contract](https://github.com/AsamK/signal-cli/blob/v0.14.5/src/main/java/org/asamk/signal/json/JsonReaction.java) and are synthetic. Live validation should include phone-number-hidden accounts, direct/group reaction addition and removal, and acknowledgments arriving before a send response. No live backend is configured in this repository.

See [development instructions](CONTRIBUTING.md) and [release notes](CHANGELOG.md).
