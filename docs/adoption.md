# Add shared behavior to an existing ADK agent

Choose the smallest supported integration below. Keep your existing agent
constructor, model, domain tools, Runner and session service while you verify
the substitution. For a coding-agent-assisted migration, start with the
[integration playbook](agent-integration.md).

For local inspection and testing of that existing constructor, use the explicit
[`projects attach` factory workflow](project-onboarding.md#existing-adk-applications).

## Choose the smallest integration

| Path | Use it when | ADKL owns |
| --- | --- | --- |
| `build_callback_adapter()` | You already construct a native ADK agent | Selected shared callbacks |
| `RuntimeBindings` | You want all four patterns in your existing native constructor | Shared selection, history, typed context and staged lifecycle; see [runtime bindings](runtime-bindings.md) |
| `CapabilityBindings` | You need one selection surface across skills, workflows and specialists | [Unified Capability Selection](capability-bindings.md), optional shared shortlisting and the shared lifecycle |
| `build_agent()` | You want ADKL to construct an agent from project files | Configured skills, tools, callbacks and presentation |

The adapter supports history slicing, tool-history repair, result projection,
result trimming, named prompt context and public-help observation. Skill-only
retrieval and typed skill context require [RuntimeBindings](runtime-bindings.md)
or the full factory. Unified Capability Selection instead uses `CapabilityBindings`
with optional `CapabilityRetrieval`; it can also compose staged presentation.
Install one selection owner, not both bindings. Public-help observation does not
enforce authorization.

## Example: project model-facing inventory fields

```python
from adkl.adapters import build_callback_adapter
from google.adk.agents import LlmAgent

shared_hooks = build_callback_adapter({
    "pipeline": ["tool_result_projection"],
    "tool_result_projection": {
        "tools": {
            "lookup_inventory": {
                "path": ["rows"],
                "fields": ["id", "available", "price"],
            }
        }
    },
})

# Existing host_model and lookup_inventory are supplied by your application.
agent = LlmAgent(
    name="inventory_agent",
    model=host_model,
    tools=[lookup_inventory],
    **shared_hooks,
)
```

Configure the shape received by the ADK callback. The projector preserves all
rows, the surrounding envelope and false/zero/null values. Unknown shapes pass
through with diagnostics; it is not a hard size or data-access limit.

## Preserve existing callbacks

Pass your existing before-model and before-tool callback lists into the adapter.
Register raw-result telemetry as `after_tool_observers` and result replacements
as `after_tool_transforms`. Observers must return `None` and should not mutate
the response. Transforms receive the latest replacement result. Put detailed,
schema-specific reduction in these application transforms. The opt-in
[generic result-size guard](tool-results.md) runs afterward and returns an
explicitly incomplete envelope instead of selecting business rows or fields.

Host before callbacks run before the shared callbacks and retain native
short-circuit behavior. Keep before/after-agent and after-model hooks on the
constructor. Do not install the adapter twice or pass the same hooks separately
to the constructor. See the [exact SDK contract](sdk.md).

## Validate before enabling

1. Record the host revision, dependencies, model and current behavior.
2. Add one feature and remove the equivalent old implementation once wired.
3. Exercise normal results, errors, empty values and host-denied tool calls.
4. Compare task outcomes and resource use using the same cases and evaluator.
5. Enable in a controlled host rollout with a tested return to the old path.

For history repair, also test parallel calls, unknown outcomes and cancellation.
The helper repairs completed outgoing batches; it does not replay tools or
support pending/resumable batches. Keep host identity and consequential-action
checks in place.

Hosts with private dependencies or existing callbacks should validate dependency
compatibility and invocation ordering before replacing their implementations.
