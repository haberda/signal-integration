# Development

Use Python 3.14 and an isolated environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
python -m pytest --timeout=30 --cov=custom_components.signal_messenger_rest --cov-report=term-missing
```

The pinned test harness installs Home Assistant 2026.9.1. Tests use mock backends and loopback HTTP/WebSocket servers; they never contact Signal or send real messages. Only tests explicitly using the socket fixture may open loopback connections.

Keep the REST client independent of Home Assistant, sanitize exceptions, and add receive fixtures without real identities or message bodies. Changes to the public event schema require an explicit compatibility decision. Keep `strings.json` and `translations/en.json` synchronized.

Before a release, test direct/group sending, attachments, receiving permissions, Assist, account linking and group/device changes against a linked test account in both the add-on and a pinned standalone API image. Check WebSocket reconnect, polling ownership, HA restart, account unlinking and failed sends. Record the backend version and results; do not infer live acceptance from mock tests.

The GitHub workflow runs tests, Ruff, Hassfest and HACS validation.

For a release, update `manifest.json` and `CHANGELOG.md` together and create a matching GitHub release after validation. Tags and publishing are separate from local development commits.

`assets/icon.svg` is the original integration icon source, covered by the repository MIT license. Regenerate its PNGs with ImageMagick when changing it. It is not an official Signal logo.
