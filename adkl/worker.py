"""Private local execution entry point. stdout is reserved for JSON-line RPC."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys


def main():
    root = pathlib.Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    protocol = sys.stdout
    sys.stdout = sys.stderr
    from adkl import capabilities, execution, features
    execution.require_local(root, os.environ.get("ADKL_PROFILE"))
    session = None

    def write(value):
        protocol.write(json.dumps(value) + "\n")
        protocol.flush()

    async def stream_turn(request):
        async for event in session.stream(request["text"], state=request.get("state")):
            write({"version": 1, **event})

    # ADK, LiteLLM and async database clients retain the same event loop across turns.
    with asyncio.Runner() as runner:
        for line in sys.stdin:
            streaming = False
            try:
                request = json.loads(line)
                if request.get("operation") == "verify":
                    from adkl.verification import evaluate
                    write(runner.run(evaluate(root, request["identity"])))
                    continue
                if session is None:
                    from adkl.chat import ChatSession
                    import yaml
                    from adkl.host import HostChatSession, entrypoint, describe_agent
                    doc = yaml.safe_load((root / "agent.yaml").read_text()) or {}
                    from adkl.agent_schema import validate
                    if entrypoint(doc) and validate(doc, root)[0]:
                        raise ValueError("Invalid host inspection descriptor")
                    session_class = HostChatSession if entrypoint(doc) else ChatSession
                    session = session_class(root, env=dict(os.environ))
                operation = request.get("operation")
                streaming = operation == "stream"
                if operation == "inspect":
                    if hasattr(session, "inspect"):
                        result = (session.inspect(startup_failure="report")
                                  if request.get("startupFailure") == "report"
                                  else session.inspect())
                    else:
                        session._build()
                        result = {"source": "adkl_factory", "agent": describe_agent(session.agent),
                                  "sessionMode": "disposable_local",
                                  "consistency": "Same project factory as SDK and release entry point.",
                                  "runtime": capabilities.manifest(features.resolve(session.doc.get("features")))}
                elif operation == "status":
                    problems = session.preflight()
                    runtime_report = (
                        session.runtime_manifest(problems) if entrypoint(session.doc)
                        else capabilities.manifest(features.resolve(session.doc.get("features"))))
                    result = {"available": not problems, "problems": problems,
                              "model": (session.doc.get("agent") or {}).get("model"),
                              "source": "host_factory" if entrypoint(session.doc) else "adkl_factory",
                              "history": session.history,
                              "runtime": runtime_report}
                elif streaming:
                    if request.get("version") != 1:
                        raise ValueError("Unsupported stream protocol")
                    problems = session.preflight()
                    if problems:
                        write({"version": 1, "type": "error", "error": "; ".join(problems)})
                    else:
                        runner.run(stream_turn(request))
                    continue
                elif operation == "send":
                    problems = session.preflight()
                    if problems:
                        result = {"workerError": "; ".join(problems)}
                    else:
                        result = runner.run(session.send(request["text"], state=request.get("state")))
                else:
                    result = {"workerError": "Unknown worker operation"}
            except Exception as exc:
                # Arbitrary project exception text may contain credentials.
                # StartupImportError text holds only descriptor names (adkl.host).
                startup = (type(exc).__module__, type(exc).__name__) == ("adkl.host", "StartupImportError")
                result = {"workerError": (
                    f"{exc}; reproduce the serving import order locally" if startup else
                    f"Worker {type(exc).__name__}; verify project dependencies and configuration")}
            if streaming:
                write({"version": 1, "type": "error", "error": result["workerError"]})
            else:
                write(result)


if __name__ == "__main__":
    main()
