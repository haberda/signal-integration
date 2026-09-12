# Signal Messenger REST

![Integration icon](custom_components/signal_messenger_rest/brand/icon.png)

A Home Assistant custom integration for [Signal CLI REST API](https://github.com/bbernhard/signal-cli-rest-api), including the [Signal Messenger add-on](https://github.com/haberda/signal-addon).

Configure accounts and notification destinations in the UI, send messages and attachments, and trigger automations from authorized incoming messages. This integration uses the separate domain `signal_messenger_rest`, so the built-in Signal integration can coexist during migration.

## Requirements and installation

- Home Assistant **2026.9.1 or newer**.
- A running Signal REST API. Select an existing account or link your Signal phone through the guided QR flow. SMS/voice registration, CAPTCHA and PIN handling remain in the backend.
- An API URL reachable **from Home Assistant**. On HAOS, `localhost` refers to the HA container, not the add-on. Use its reachable hostname or host IP and port.

For HACS, add the public GitHub mirror of this repository under **HACS → Custom repositories**, select **Integration**, download **Signal Messenger REST**, and restart Home Assistant. Then open **Settings → Devices & services → Add integration → Signal Messenger REST**.

This checkout currently uses a self-hosted Git remote. HACS supports public GitHub repositories, so a public mirror must exist before that installation route works. Repository structure, metadata, local brand assets, and validation workflows are included. No GitHub repository or release is created automatically. [HACS hosting requirements](https://www.hacs.xyz/docs/faq/other_git_providers/).

For local testing before publication, copy `custom_components/signal_messenger_rest` into your HA configuration's `custom_components` directory and restart HA.

## UI setup

1. Select a detected running Signal add-on, or enter the backend URL manually. Path prefixes are supported. Optional username/password fields are for an HTTP Basic-auth reverse proxy, not a Signal account password. TLS certificate verification defaults to on.
2. Choose an existing account, or select **Link a phone account using a QR code**. Name the backend device, scan the displayed code in Signal’s **Settings → Linked devices**, approve on the phone, and check for the account before selecting it.
3. Select contacts/groups or type recipient numbers, UUIDs, usernames, or REST `group.` IDs manually. At least one notification destination is required; **Note to self** is available even before contacts have synchronized. Select **Customize destination names** to enter an optional name for each destination on the following screens. Leave a name blank to use the contact/group name or recipient identifier. The account and destination names determine the friendly name and initial entity ID. Changing a destination name later updates its default friendly name; existing entity IDs and names overridden in Home Assistant are preserved. You can edit those in the entity settings.
4. Enable receiving if needed, and select allowed contacts/groups by name or enter sender numbers/UUIDs manually. An empty list allows **no incoming message events**. Group messages require both an allowed sender and an allowed group ID.
5. Optionally select a test destination before submitting. This sends one fixed test message to that destination only. The default sends nothing. A failed or uncertain test leaves settings unsaved and resets the test selection; saving again does not automatically resend.

Use integration **Configure → Destinations and receiving** to change destinations, receiving permissions and polling intervals. Use **Reconfigure** to change the URL or proxy credentials while retaining the same Signal account. A proxy authentication failure starts a credential recovery flow. Changing options reloads the integration.

### Add-on discovery

On Home Assistant OS/Supervised, setup detects these running add-ons through Supervisor:

| Repository | Add-on slug | Default internal URL |
| --- | --- | --- |
| Production | `1315902c_signal_messenger` | `http://1315902c-signal-messenger:8080` |
| Edge | `c5ecc243_signal_messenger` | `http://c5ecc243-signal-messenger:8080` |

Setup uses the hostname reported by Supervisor and the container's port 8080, independently of host port mappings. If both are running, choose one. Stopped add-ons are ignored; setup does not start them. Manual connection remains available, including when Supervisor data is unavailable or the internal connection fails. Standalone Home Assistant installations use the manual URL form.

### Guided account linking and devices

QR linking uses the backend's `GET /v1/qrcodelink/raw?device_name=...` endpoint, reviewed against REST API 0.100. Home Assistant renders the returned Signal URI using its native QR selector. The backend owns the Signal handshake; no separate browser connection to the add-on or publicly hosted image is needed. Connection credentials, proxy path prefixes and TLS settings apply to these requests.

An active linking attempt reuses its QR code when the form is redisplayed. Select **I scanned the code — check for my account** to refresh account discovery; this does not create another handshake. The QR display expires after five minutes (Signal may expire the handshake earlier). **Request a new QR code** returns to the device-name form and starts a new attempt only when submitted. Closing the dialog cannot cancel a handshake already started by the backend. If you relinked an account already present on the backend, complete linking on the phone, then use **Choose an existing account**.

QR credentials are held only in the active flow; they are not stored in config entries, diagnostics, or files. If the raw linking endpoint is unavailable, link in the backend and use **Refresh account list** during setup. Registration codes, CAPTCHA and PINs are not collected by this integration.

Open **Configure → Accounts and linked devices** to view the current entry's account and all accounts available on its backend. You can link another phone account there, but doing so does not switch the current entry. Add a separate integration entry to configure the other account. Duplicate backend/account entries are rejected.

Under **View and manage linked devices**, you can view device names, IDs, creation times and last-seen times (UTC). Device administration depends on backend support and account role:

- **Link another device** accepts a companion's Signal provisioning URI when the backend is the primary account. This differs from displaying a QR code to link the backend to your phone.
- **Remove a linked device** excludes the primary device. Removing the backend's own companion device can disconnect this integration.
- Both operations require confirmation. Failed or uncertain mutations are not retried automatically. Reopen the device list or check Signal on your phone before repeating them.

A backend linked to your phone may reject device administration; in that case use Signal's **Linked devices** settings. Removing the Home Assistant integration itself never unlinks a Signal device or deletes backend account data.

### Group management

Open **Configure → Manage Signal groups** to create a group or manage an existing one. You can:

- Create a named group with initial members and a description.
- View current membership, administrators, pending invitations/requests, and the invite link.
- Change the name, description, disappearing-message timer, invite-link state, and editing/member/message permissions.
- Add or remove members, promote or demote administrators, and leave a group.

Enter member numbers in international format or use Signal UUIDs. The integration account needs the relevant Signal permissions; the backend may reject unsupported operations. Timer values are seconds, with 0 disabling disappearing messages. An omitted timer or an **unchanged** permission/link selection preserves that setting, since the list API does not report all current settings.

Every mutation has a review and confirmation screen. Changes apply immediately to Signal; closing the dialog does not undo them. If a request fails or times out, the flow ends without retrying. Check Signal and backend logs before repeating an operation, because it may already have succeeded.

When creating a group, select **Add as notification destination** to create its notifier automatically after confirmation. This option defaults to off. Otherwise, reopen **Destinations and receiving** to select the group later. Incoming group events always require separate authorization; the checkbox does not change incoming allowlists. Leaving a group does not automatically remove its saved destination or permissions; remove those in settings if needed. Existing entity names can be renamed in Home Assistant.

The backend’s delete endpoint calls `quitGroup` in version 0.100, so the UI exposes this as **Leave group**. It does not delete the group for everyone.

Group operations use the [upstream REST API group endpoints](https://github.com/bbernhard/signal-cli-rest-api/blob/0.100/src/api/api.go). Group avatars, joining via invite links, and approving pending join requests are not exposed in this release.

## Assist conversations

Signal can send text directly to an existing Home Assistant Assist pipeline and return its response to the originating chat. No automation is required. Configure an assistant in Home Assistant first; the integration uses the selected pipeline's conversation agent and language, running only the intent stage.

1. Under **Configure → Destinations and receiving**, enable receiving. Keep the existing polling-mode guidance in mind.
2. Open **Configure → Assist conversations**, enable Assist, and select your pipeline.
3. Explicitly select allowed sender phone numbers/UUIDs. For group chats, also select the allowed group.
4. Choose direct-chat activation: require a prefix (default `/assist`) or process all direct text messages. Groups always require the prefix.
5. Set an idle timeout and save. For example, send `/assist turn on the kitchen lights`.

Assist permissions are separate from message-event permissions. An empty Assist sender list denies all Assist access, and a group alone never authorizes every member. Authorized senders can use the selected agent's configured capabilities and exposed controls; Signal identities are not mapped to individual Home Assistant users.

Replies go to the originating direct or group chat; group replies quote the original command and are visible to that group's members. Voice notes and attachments are not processed by this feature. Replies are text only.

Enable **Show typing while Assist responds** in Assist settings to attempt to show a typing indicator in the active direct or group conversation. It defaults to off. The indicator starts when processing begins, refreshes every eight seconds, and stops after reply delivery, failure, or cancellation. Queued and rejected requests do not start indicators. Each indicator operation has a two-second deadline; a failed start or refresh ends further refreshes for that request without interrupting Assist. Stopping is best-effort if the backend is unreachable. The backend provides [typing indicator endpoints](https://github.com/bbernhard/signal-cli-rest-api/blob/master/src/api/api.go).


Conversation context is isolated by integration account, chat and sender. Follow-up requests reuse a Home Assistant conversation ID when available. After the configured idle interval (30–300 seconds), the next request starts fresh; an agent may retain context for less time. Send `/assist /reset` to start a new conversation immediately, replacing `/assist` if you chose another prefix. In all-direct-messages mode, `/reset` alone also works. Reset starts a new conversation; it does not delete Home Assistant's existing traces or agent history.

By default, requests handled by Assist are not also published as `signal_messenger_rest_message_received` events. This avoids duplicate replies from existing automations. **Also publish handled messages as events** enables that behavior, but the normal event sender/group permissions still apply. Ordinary messages and reactions retain their existing event behavior.

Enable **Include quoted text as Assist context** to supply up to 2,000 characters of a replied-to message alongside your new request. For example, quote a reply naming bedroom lamps and ask `/assist set those to 5%`. The excerpt goes to the selected Assist agent, including its cloud provider if configured. Quotes are labeled as untrusted reference text, not separate commands; interpretation depends on the agent, and this labeling is not an enforcement boundary for an LLM. Existing sender/group permissions and prefix rules apply to the new message. The setting defaults to off. Quotes do not restore expired conversations or bypass `/reset`, and quoted attachments are not fetched. Plain requests without quoted text are unchanged.

The selected pipeline's **Prefer handling commands locally** setting controls routing. When off, the selected conversation agent handles Signal requests without interception by sentence-trigger automations. When on, Home Assistant may answer matching commands locally. Changing the pipeline's agent, language or local-handling preference starts fresh Signal conversation context so a pending follow-up cannot retain the old agent.

Use **Configure → Assist pipelines by destination** to assign a pipeline to a direct-chat sender number/UUID or REST group ID. Unmapped conversations use the default pipeline. A UUID override wins over a phone-number override in direct chats; group routing uses only the group ID. Overrides do not grant permission or change activation rules. Select **Use default pipeline (remove override)** to remove an assignment. Missing pipelines fail without silently switching agents. Up to 64 overrides are supported; editing an assignment reloads the entry and clears its context. The destination screen also shows the assigned pipeline and agent for each override.

Open **Configure → Assist troubleshooting** to inspect the default pipeline and agent, activation mode, API/receiver status, saved/running consistency, queue and last error. Refresh the screen to update its snapshot. **Clear conversation sessions** forgets this account's context references after confirmation; it does not cancel active commands or delete provider history. An in-flight result cannot restore a cleared session.

For unexpected responses, download integration diagnostics after reproducing the issue. `assist_running_activation` and `assist_configured_activation` should agree; `assist_pipeline_matches_options` should be true. `assist_last_route` describes the last completed intent response using only booleans: `selected_agent_is_local`, `prefer_local_intents`, and `processed_locally`. For Gemini with local handling disabled, all three should be false. No prompts, replies, account identifiers or agent names are included. With prefix activation selected, unprefixed text is not submitted to Assist by this integration.

Each account processes one Assist request at a time with at most 16 queued requests. Requests waiting longer than 60 seconds, oversized inputs (over 4,000 characters after the prefix), and excess requests are rejected without execution. A reply explains the reason and quotes the affected request. Rejection notices are limited to five per account per minute, with a separate bounded queue; notices waiting longer than 60 seconds are suppressed. Unauthorized messages and duplicates do not receive these notices. Replies are limited to 10,000 characters. Up to 64 conversation references are retained per account.

The account device has diagnostic sensors for **Assist status**, **Assist queued requests**, **Assist completed requests**, **Assist errors**, **Assist rejected requests**, **Assist feedback suppressed**, and **Assist last error**. Status distinguishes disabled receiving, disconnection, unavailable pipelines, processing, and idle. Queued requests exclude the active request. Completed requests count successful pipeline runs, even if reply delivery later fails. Errors count pipeline, reply, and feedback failures, so one request can cause multiple errors. The last error remains visible after later successes. Counters and the last error reset when the entry reloads or Home Assistant restarts. Downloaded diagnostics also break rejected requests down into busy, oversized, and expired counts. These states contain no message content or sender identifiers.

A pipeline run has a 60-second timeout. Pipeline and reply failures are never retried automatically because an action or send may already have succeeded. Reload/unload/shutdown cancels pending work and clears the integration's conversation references. Duplicate suppression is in memory and does not guarantee replay protection across restarts.

The integration does not log prompts or agent error details, but Home Assistant's Assist debug history, the selected agent/provider, and optionally automation traces can retain conversation content. Live acceptance testing should cover the selected local or cloud agent, exposed entity controls, group replies, follow-ups, and backend disconnects.

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

Receipt actions, message editing/deletion, polls, and SMS/voice account registration remain outside version 0.5.0.

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

Before a public release, validate these cases on a linked test account: direct/group/self sending; a local and URL attachment; a quoted reply to direct/group messages; two identical incoming texts; a sender without a phone number; unauthorized sender/group filtering; backend restart; HA restart/reload; a backend mode change; account unlinking; send timeout and partial failure. Check add-on and standalone deployments independently. Verify production/edge discovery, both add-ons running, manual fallback, and group creation/settings/membership/admin/leave changes with a test group. Validate QR scanning and approval, account discovery, QR expiry/replacement, multiple accounts, and device listing/addition/removal on both primary and companion backends. Never send real messages from automated CI.

### Read receipts

Enable **Send read receipts** under **Configure → Destinations and receiving**, with receiving enabled, to tell senders when the integration reads their messages. The option defaults to off.

Receipts are sent for messages accepted by the incoming sender/group permissions or handled by Assist, including requests Assist rejects as busy, oversized or expired. A read receipt means the integration received and handled the message; it does not confirm that an automation or Assist command succeeded. Filtered messages, reactions and duplicates are not acknowledged. For group messages, receipts go to the original sender.

The integration uses the backend's [read receipt endpoint](https://github.com/bbernhard/signal-cli-rest-api/blob/master/src/api/api.go), supporting both polling and WebSocket receiving. Receipts run separately from message processing, with at most 128 pending receipts and a 15-second deadline per attempt. Failed sends are not retried, and pending receipts are canceled on reload or shutdown. Downloaded diagnostics include `read_receipts_sent`, `read_receipts_failed`, `read_receipts_dropped` and `read_receipts_queued`; counters reset on reload. Check these if senders still do not see read receipts after enabling the option.

### Receiving troubleshooting

| Symptom | Check |
| --- | --- |
| API unreachable | URL as seen from HA, add-on/container status, port, TLS and proxy credentials |
| API reachable, receiver disconnected | Backend mode and WebSocket proxy support; reconfigure credentials if requested |
| Connected but no events | Incoming sender/group selections and the `filtered_events` diagnostic count; group events require both permissions |
| Some polling messages missing | Disable competing receive sensors, helper receivers and backend automatic receive schedules |
| Text arrives but reaction does not acknowledge | React to the original alert, check exact emoji/author/conversation, and supply `account_author` if the account number is hidden |
| Reminders stop after restart/reload | Expected: pending alerts are in memory and canceled on reload |
| Assist does not respond | Enable receiving and Assist; check the separate Assist sender/group allowlists, prefix and selected pipeline |
| Assist replies twice | Disable publishing handled Assist messages as events, or remove overlapping reply automations |
| Assist drops requests | Check Assist rejected requests and Assist feedback suppressed on the account device; downloaded diagnostics include rejection reasons |
| Assist reports an uncertain failure | Inspect the resulting device state before repeating the command; it was not retried |

The diagnostic `pending_alerts` count reports current waits without message contents or identities. Reaction fixtures were derived from the [signal-cli v0.14.5 JSON contract](https://github.com/AsamK/signal-cli/blob/v0.14.5/src/main/java/org/asamk/signal/json/JsonReaction.java) and are synthetic. Live validation should include phone-number-hidden accounts, direct/group reaction addition and removal, and acknowledgments arriving before a send response. No live backend is configured in this repository.

See [development instructions](CONTRIBUTING.md) and [release notes](CHANGELOG.md).

## AI assistance

This integration was created with AI assistance, including help with planning, implementation, documentation, and automated tests.
