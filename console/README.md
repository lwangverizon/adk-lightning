# ADKL Console

Local Flutter web UI for skill editing, native-agent inspection, disposable test
conversations and Doctor findings. This is not a multi-user production server.

## Develop

Use **Flutter 3.44.0** and the committed dependency lock. From this directory:

```bash
flutter pub get --enforce-lockfile
flutter analyze
flutter test
flutter build web --release
```

Do not regenerate the lock to accommodate a different Flutter version. From the
repository root, prepare the credential-free native example and serve the build:

```bash
uv sync --locked --extra runtime
uv run --no-sync python scripts/build_sdk.py
uv run --no-sync python -m adkl.projects prepare projects/native-agent --wheel dist/adk_lightning-0.1.0-py3-none-any.whl
uv run --no-sync python -m adkl.server --root projects/native-agent --web console/build/web
```

Open `http://127.0.0.1:8787`. Inspect the project, then send `Show v0.9`,
`Show v1.0` and `thanks` in Test. These use a scripted model, not a provider key.
For hot reload run `flutter run -d chrome` here with the API server running.

## Entry points and ownership

- `lib/main.dart`: local authoring/testing console.
- `lib/portal_main.dart`: read-only offline snapshots, no live execution.
- `lib/connected_main.dart`: read-only host-authorized declarations.
- `lib/api.dart`: local HTTP client and request deadlines.
- `test/`: widget and client regression tests; browser-specific tests are separate.

Start with [authoring](../docs/authoring.md), [development](../docs/development.md)
and the [inspection choice guide](../docs/architecture.md#choose-an-inspection-surface).
Use [browser media checks](../scripts/test_a2ui_browser.sh) for browser-only media
behavior; a widget test or successful build alone does not prove browser UX.

SDK and Console archives are packaged separately. See
[installation](../docs/project-onboarding.md#install-the-sdk-and-console) and
[release requirements](../docs/sdk-releases.md). No Flutter build publishes a site.
