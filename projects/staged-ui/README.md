# Editable skill with a staged step card

This small YAML project uses `adkl/common@1` and the `render_steps` tool. Its
`explain` skill stages supplied steps; ADKL emits the card after the turn and
the console renders the shared A2UI presentation. It also enables history slicing.
There are no domain backends or application-specific renderers.

From the ADKL checkout (Python 3.13, uv):

```bash
uv sync --locked
uv run --no-sync python scripts/build_sdk.py
uv run --no-sync python -m adkl.projects prepare projects/staged-ui --wheel dist/adk_lightning-0.1.0-py3-none-any.whl
uv run --no-sync python -m adkl.server --root projects/staged-ui --web console/build/web --sdk-wheel dist/adk_lightning-0.1.0-py3-none-any.whl
```

Build the console first using the [authoring setup](../../docs/authoring.md).
Open `http://127.0.0.1:8787/`, save a Google API key in **Project**, and open
**Test**. Try: “Show these steps as a card: write a draft, review it, publish it.”
Inspect the staged widget and model-call trace. Edit the short `explain` skill
to explore the authoring workflow. Interactive responses require a provider
and incur its normal usage; the deterministic SDK tests inject a fixture model.

For a credential-free interactive example with both protocol versions, use
[native-agent](../native-agent/README.md). The console gallery at
`?a2ui-gallery=1` shows all 18 basic primitives; the sample card intentionally
stays small. Read [A2UI support](../../docs/a2ui-support.md) for interaction and
protocol limits. See [onboarding](../../docs/project-onboarding.md) to create a
separate use-case project and prepare a deployment bundle.
