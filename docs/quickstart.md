# Run your first ADKL SDK example

This example runs a real native ADK Runner with a synthetic model and tool.
It requires no credentials, network calls from the agent, or external checkout.
Dependency installation needs access to the public package registry.

## 1. Install the locked runtime

From the repository root, with Python 3.13 and uv available:

```bash
uv sync --locked --extra runtime
```

The base package is lightweight. This example needs the optional ADK runtime.
It uses the repository lockfile and does not change it.

## 2. Run the example

```bash
uv run --no-sync python -m examples.sdk_smoke
```

Expected output, alongside any dependency warnings:

```text
PASS: the model saw id and available; internal_notes was omitted.
```

The [example source](../examples/sdk_smoke.py) returns a synthetic inventory row.
ADKL projects the declared fields before the second model call. The fixture
raises an error if the projected result differs, including if `false` is lost.

This proves the local callback wiring with synthetic data. It does not test a
live model's reasoning or an external tool, and projection is not a privacy gate.

## 3. Choose your integration path

| Your application | Next guide |
| --- | --- |
| Already constructs native ADK agents | [Adopt shared callbacks](adoption.md) |
| Needs one selection surface across skills, workflows and specialists | [Unified Capability Selection](capability-bindings.md); run `uv run --no-sync python -m examples.capability_selection` |
| Should be built from ADKL project configuration | [Use the SDK factory](sdk.md) |
| Needs a graphical skill editor | [Run the console](authoring.md) |
| Already produces baseline/candidate test results | [Compare evaluation evidence](evaluation.md) |

For an external host during the current pre-release phase, follow the
[pinned-checkout wheel workflow](agent-integration.md#consume-a-pinned-main-checkout-today).
Use `python scripts/build_sdk.py`, not a source import or editable dependency:
it stages only the package and avoids carrying repository projects, examples or
credentials into the wheel. Preserve private ADK sources inside the environment
that owns them; see [incremental adoption](adoption.md).
