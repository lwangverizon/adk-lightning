"""Synthetic model, real native ADK execution and skill-context assertions."""
from pathlib import Path
import time

from adkl.contract_probes import state_isolation
from adkl.host import HostChatSession


async def evaluate(*, case, repetition):
    started = time.perf_counter()
    session = HostChatSession(Path(__file__).resolve().parent, env={})
    try:
        result = await session.send(case["input"])
        widgets = result["trace"]["widgets"]
        request = session.inspect().get("lastRequest") or {}
        passed = (result["trace"]["error"] is None and len(widgets) == 1
                  and widgets[0]["a2ui"]["version"] == case["version"]
                  and not widgets[0]["a2ui"]["warnings"]
                  and case["required_instruction"] in request.get("instruction", ""))
        followup = await session.send("thanks")
        passed = passed and state_isolation(
            {"error": followup["trace"]["error"], "widgets": followup["trace"]["widgets"]},
            expected={"error": None, "widgets": []})
        return {"passed": bool(passed), "latency_ms": (time.perf_counter() - started) * 1000,
                "cost_usd": 0.0}  # No model provider or paid service is invoked.
    finally:
        if session._runner:
            await session._runner.close()
