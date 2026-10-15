"""Output filtering and staged presentation declarations."""


from .model import Kind, Spec, Stage


SPECS = (
    Spec(
        name="output_cleanup",
        support_status="implemented",
        support_detail=(
            "Preview removes structured-output leaks from completed model responses."
        ),
        kind=Kind.AFTER_MODEL,
        summary="Strip structured-data leaks from a completed model reply.",
        stage=Stage.EXPERIMENTAL,
        reads=frozenset({"llm_response.content"}),
        writes=frozenset({"llm_response.content"}),
        options={
            "report_removals": ("bool", True,
                                "Record removed structured output in the turn trace."),
        },
    ),
    Spec(
        name="pii_redaction",
        support_status="implemented",
        support_detail=(
            "Preview masks configured identifier classes in completed model "
            "responses."
        ),
        kind=Kind.AFTER_MODEL,
        summary="Mask configured customer identifiers a model echoed in its reply.",
        stage=Stage.EXPERIMENTAL,
        reads=frozenset({"llm_response.content"}),
        writes=frozenset({"llm_response.content"}),
        must_run_after=frozenset({"output_cleanup"}),
        options={
            "kinds": ("list", ["ssn", "account", "phone", "email"],
                      "Identifier classes to mask in the customer reply."),
        },
    ),
    Spec(
        name="staged_widgets",
        support_status="implemented",
        support_detail=(
            "Factory and host bindings attach staged contracts and emission callbacks; "
            "host bindings accept explicit widget and companion delivery sinks. "
            "Versioned presentation packs provide agent-level tools and native "
            "primitive display. Action submission is unavailable. Configured legacy "
            "widget hooks must still load successfully."
        ),
        kind=Kind.AGENT_CALLBACK,
        summary="Run the stage -> rerank -> contract -> emit pipeline for declared "
                "A2UI widget specs, draining live registers at the turn boundary.",
        stage=Stage.EXPERIMENTAL,
        # Deliberately ONE feature covering two hooks. The before-model half resolves
        # the reply-shaping contract; the after-agent half emits. Split into two
        # features, one could be activated without the other -- which is exactly the
        # failure this is meant to prevent: a contract shaping a reply for UI that
        # never arrives, or UI arriving on a turn whose reply was never shaped for it.
        reads=frozenset({"session.state"}),
        writes=frozenset({"session.state:staged_widgets", "event.actions.render_ui_widgets",
                          "llm_request.instructions"}),
        # Registers are staged by tools, and which tools are bound depends on which
        # skill was retrieved. Emitting before that is settled would drain a register
        # whose stage tool was never available.
        must_run_after=frozenset({"skill_retrieval", "prompt_state_injection"}),
        # Type is the NAME of the type, not the type. Every other feature here does
        # the same; writing `bool` instead of `"bool"` put a Python class into
        # describe()'s output and 500'd /api/agent with "Object of type type is not
        # JSON serializable" -- taking the whole console down, since it loads the
        # agent before rendering anything.
        options={
            "inject_contract": ("bool", True,
                                "Inject the resolved presentation contract before "
                                "the model call. Off leaves instruction shaping to "
                                "the host; contract recording and emission remain active."),
            "isolate_render_failures": ("bool", False,
                                        "Decline a failing widget sink per spec and continue "
                                        "unrelated widgets; never retry partial delivery "
                                        "within the invocation. Cancellation still propagates."),
            "request_id": ("str", "adkl",
                           "Correlation id used in emit telemetry."),
        },
    ),
)
