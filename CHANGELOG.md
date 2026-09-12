# Changelog

## 0.5.2

- Explain busy, oversized and expired Assist requests with quoted, rate-limited replies.
- Bound feedback work separately from pipeline work and cancel both on unload; never retry failed notices.
- Add content-free diagnostic sensors for Assist status, queue length, completed runs, errors, rejected requests, suppressed feedback and last error.
- Include rejection reason counters in downloaded diagnostics.

## 0.5.1

- Honor disabled local handling by using the selected agent without sentence-trigger interception.
- Start fresh conversation context when a pipeline's agent, language or local-handling preference changes, preventing retained follow-ups from using the old agent.
- Add content-free routing diagnostics for saved/running activation, pipeline selection consistency and the last completed response's local-processing status.
- Cover repeated non-local replies, intentional local handling and mid-conversation agent switching with real Home Assistant pipeline tests.

## 0.5.0

- Add direct text conversations with a selected Assist pipeline, without user-created automations.
- Configure separate Assist allowlists, direct-chat activation, a mandatory group prefix, idle timeout and optional event publishing.
- Isolate conversation references per account/chat/sender and support a reset command.
- Bound queued work, input/output sizes, session references and execution time; cancel work on unload and never automatically retry uncertain operations.
- Reply to the originating chat and quote group commands.
- Preserve Assist options when updating notification destinations and add content-free Assist diagnostics.
- Validate text-only operation against Home Assistant's real Assist pipeline.

## 0.4.0

- Guide setup through existing-account selection or in-dialog QR linking, including empty backends and account-list refresh.
- Reuse QR credentials during a linking attempt, hide expired codes, and require explicit regeneration.
- Offer Note to self during destination setup before contacts synchronize.
- Add an account overview and QR linking in options without switching the entry's bound account.
- List linked devices with timestamps and provide confirmed addition/removal, subject to backend account-role permissions.
- Keep provisioning credentials out of saved entries and avoid automatic mutation retries.

## 0.3.2

- Add an optional notification-destination checkbox when creating a group; confirmation saves the returned group ID and reloads notification entities.
- Preserve existing destinations and incoming permissions.
- Clarify that the supported backend's delete endpoint leaves the group rather than deleting it for everyone.

## 0.3.1

- Include explicit labels in both options menus so buttons remain visible when frontend translations are stale or unavailable.
- After updating, restart Home Assistant and refresh the browser to reload page titles and field translations.

## 0.3.0

- Discover running production and edge Signal add-ons and connect through Supervisor-reported internal DNS on port 8080.
- Retain manual backend configuration and selection when multiple add-ons are running.
- Add an options menu for notification settings and group management.
- Create groups, view group details, edit names/descriptions/timers/invite links/permissions, manage members and administrators, and leave groups.
- Require confirmation for group changes and avoid automatic retries after uncertain failures.
- Preserve existing notification destination IDs and incoming permissions.

## 0.2.0

- Send and remove reactions through dedicated actions.
- Receive authorized reaction events with original-message references, emoji and removal status.
- Send expiring alerts with reminders and acknowledgment matching; listeners are installed before sending and cleaned up on cancellation/unload.
- Add a binary-sensor alert blueprint that stops on acknowledgment, sensor resolution or expiry.
- Select incoming permissions from named contacts/groups; retain manual identifiers.
- Optionally send one deliberate setup test without persisting or automatically retrying that choice.
- Add filtered-event and pending-alert diagnostic counts.
- Keep message event schema version 1 and existing configuration entries compatible.

Live Signal/add-on acceptance and HACS installation remain pending; automated tests use synthetic fixtures and loopback servers.

## 0.1.0

First local release candidate; live Signal acceptance testing is still required.

- UI setup, account selection, named destinations, options, reconfiguration and proxy credential recovery.
- Modern notification entities and a rich send action with attachments, formatting, quoted replies and per-destination response data.
- WebSocket receiving in JSON-RPC modes and polling in normal/native modes.
- Authorized incoming message events, content-free event entities, deduplication and automatic reconnect.
- Separate API/receiver connectivity diagnostics and message timestamps.
- HACS packaging, local brand assets, an automation blueprint and validation workflows.
