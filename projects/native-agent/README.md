# Native ADK agent with staged A2UI

This example keeps the ADK constructor in [demo_agent.py](demo_agent.py) and uses
`RuntimeBindings` for skill selection, history slicing, state injection and
staged widget emission. A domain tool stages an A2UI card; ADKL emits it after
the model turn and renders it in the console. No application converter is needed.

The model and embedder are scripted fixtures. ADK tool execution, callbacks,
widget emission and A2UI reduction are real; this does not measure model quality.

The `display` skill is loaded from `skills/display/SKILL.md` and explicitly mapped
in `agent.yaml`. The original console editor can save its description, procedure
and tool bindings to that file. Its dedicated Findings panel supports selected
content and all-agent issues. Unmapped native declarations remain read-only.

From the ADKL checkout (Python 3.13, uv):

```bash
uv sync --locked
uv run --no-sync python scripts/build_sdk.py
uv run --no-sync python -m adkl.projects prepare projects/native-agent --wheel dist/adk_lightning-0.1.0-py3-none-any.whl
projects/native-agent/.venv/bin/python -I projects/native-agent/verify.py
uv run --no-sync python -m adkl.server --root projects/native-agent --web console/build/web --sdk-wheel dist/adk_lightning-0.1.0-py3-none-any.whl
```

Build the console first using the [authoring setup](../../docs/authoring.md).
Open `http://127.0.0.1:8787/`. In **Project**, inspect the actual native agent.
In **Test**, send `Show v0.9`, then `Show v1.0`. Each reply contains a card with
two steps and companion text. Send `thanks` to verify that the previous card is
not emitted again. Clear starts a new conversation. The generic YAML skill editor
does not rewrite this Python constructor; edit domain code in its host repository.

Send `Show shared pack` to exercise the built-in common presentation tools through
the same native binding. Project inspection exposes native skill content, tool
schemas and static findings; after Test, expand the last local request. Turn traces
include the shared widget emission gates.

In **Project → Verify skill changes**, record a baseline, save a display-skill
edit, and verify the candidate. The configured evaluator runs real native ADK
turns with the synthetic model, checks card behavior and the expected instruction
in the model request. Removing `acknowledge thanks without a card` causes a
regression; restore it and rerun. See [the verification guide](../../docs/verification.md)
for CLI commands, identity boundaries and adapting the evaluator to a real host.

To try this outside ADKL, copy this small directory (without `.venv`, `.adkl` or
`vendor`) to your application workspace and repeat `prepare` using its new path.
The project gets its own wheel, lock and interpreter. For a real migration,
replace the fixture model, embedder, skill and tool with your host implementations;
retain the binding lifecycle. See [the contract](../../docs/runtime-bindings.md)
and [integration guide](../../docs/agent-integration.md).
