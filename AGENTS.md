# Working on Signal Messenger REST

These instructions apply to the whole repository. Follow the user's current task and preserve unrelated work in the checkout.

## Project

This is a Home Assistant custom integration for `signal-cli-rest-api` and the Signal Messenger add-on. Its domain is `signal_messenger_rest`, separate from Home Assistant's built-in Signal integration. It supports UI configuration, notification entities, rich sending, incoming events, account/group/device management, and direct Assist conversations.

Read `README.md` for setup, `docs.md` for usage, and `CONTRIBUTING.md` for development. Check the manifest and changelog for the current version; do not assume a version from previous conversation history.

## Architecture

Source lives in `custom_components/signal_messenger_rest/`:

- `api.py`: asynchronous REST/WebSocket client, independent of Home Assistant.
- `models.py`: incoming event normalization and identity/permission checks.
- `receiver.py`: receiving, deduplication, reconnect and cancellation.
- `coordinator.py` and `__init__.py`: account runtime, dispatch, setup and teardown.
- `config_flow.py`, `*_flow.py`, `addon.py`: configuration and account/group/device UI.
- `assist.py`: authorization, activation, bounded processing and conversation context. `typing_indicator.py` and `receipts.py` provide best-effort feedback.
- `services.py`, `alerts.py`, `attachments.py`: rich actions, acknowledgment waits and bounded attachment handling.
- Entity platforms and `diagnostics.py`: user-facing entities and content-free operational information.

Automation blueprints live in `blueprints/automation/signal_messenger_rest/`. They are imported separately into Home Assistant, not installed by HACS. `notify.send_message` handles text; attachments use `signal_messenger_rest.send_message`.

## Behavior to preserve

- Event permissions and Assist permissions are separate. A group requires both an authorized sender and an authorized group. Names are not identities. Never evaluate incoming text as a template, action name or shell command.
- Preserve prefix activation, account/chat/sender context isolation and explicit pipeline assignments. Do not silently fall back to a different agent when an assigned pipeline is unavailable.
- Quoted Assist context is opt-in and must disclose that excerpts go to the selected agent/provider. Quoted text does not grant permission or independently activate Assist.
- Do not automatically retry uncertain sends or account/group/device mutations. A timeout can happen after an operation succeeds.
- Bound queues, response bodies, attachments, retained context and operation durations. Cancel background work and remove listeners on unload/shutdown. Let task cancellation propagate.
- Keep blocking file work off the event loop. Preserve path/URL allowlists, redirect checks, TLS verification and file-size limits.
- Preserve entity unique IDs when names/settings change. Public event fields, action schemas and blueprint input names require compatibility consideration.
- Keep `strings.json` and `translations/en.json` synchronized. Options menus use explicit labels to remain usable with cached frontend translations.
- The Assist bridge uses Home Assistant's private `_intent_agent_only` flag for routing correctness. Changes here require real Home Assistant pipeline regression tests; changing intent stages alone is not an equivalent replacement.

## Privacy and backend contracts

Do not log or include raw backend errors, message bodies, credentials, QR provisioning data or private identifiers in diagnostics. Use synthetic test data. If improving error reporting, classify recognized errors into safe messages instead of exposing backend response bodies.

Verify endpoints and payloads against upstream documentation/source before implementing backend features. A signal-cli command is not necessarily exposed by the REST API. Do not invent endpoints or change recipient identifiers based only on a generic HTTP error.

Automated tests must not send real Signal messages or mutate live accounts, contacts, groups or devices. Use mocks and local HTTP/WebSocket servers. Live acceptance is separate and requires an explicitly designated environment and authorized operations.

## Validation

Use Python 3.14 and the repository's pinned development dependencies. Prefer the existing `.venv`:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest --timeout=30 --tb=short
```

If needed, create `.venv` and install `requirements-dev.txt`. Run focused tests while iterating, then the full suite for runtime or shared configuration changes. For documentation-only edits, check links and `git diff --check`; do not add tests that merely mirror prose.

For blueprint behavior changes, execute substituted blueprints with Home Assistant's automation/script engine, not just a YAML parser. Cover relevant triggers, permissions, failure paths, ordering and cancellation. Camera tests must mock capture/send operations. Keep blockers checked before captures and sends, preserve cooldown after handled failures, and retain independent snapshot/video attempts.

CI in `.github/workflows/validate.yaml` runs Ruff, pytest, Hassfest and HACS validation. Run applicable metadata validation when changing manifests, schemas or translations. Report exactly which checks ran and distinguish mocked tests from live backend verification.

## Workflow and documentation

- Inspect `git status` before editing. Preserve user changes, including file renames.
- Implement authorized work without unnecessary confirmation. Ask when essential information is missing; do independent work while waiting.
- Commit each logical chunk locally with a descriptive message. Stage specific files. Do not push, tag, publish releases or send external messages unless requested.
- Keep ignored planning/assessment artifacts out of commits and do not reference them in tracked documentation or code.
- Keep the README short: installation, setup, basic usage and links. Put practical feature details in `docs.md`; development instructions belong in `CONTRIBUTING.md`.
- Do not add discussion of Git hosting topology or mirroring to usage documentation. Preserve the AI-assistance acknowledgment in the README.
- Document behavior changes in `CHANGELOG.md`. Use its Unreleased section for unreleased work. When preparing a release, update the manifest version, changelog and corresponding metadata assertions together.
- Final reports should briefly state changes, validation, local commits and any real limitations. Never claim a live test or deployment that did not occur.
