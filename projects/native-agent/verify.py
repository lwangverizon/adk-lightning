"""Run the installed SDK against this host-owned agent without credentials."""
import asyncio
from pathlib import Path
import sys

import adkl
from adkl.host import HostChatSession


async def verify():
    assert Path(adkl.__file__).is_relative_to(Path(sys.executable).parent.parent)
    session = HostChatSession(Path(__file__).resolve().parent, env={})
    try:
        for version in ("v0.9", "v1.0"):
            result = await session.send("Show " + version)
            assert not result["trace"]["error"], result
            assert len(result["trace"]["widgets"]) == 1, result
            view = result["trace"]["widgets"][0]["a2ui"]
            assert view["version"] == version and view["root"] == "root", view
            assert not view["warnings"], view
        result = await session.send("thanks")
        assert not result["trace"]["widgets"], result
        result = await session.send("Show shared pack")
        assert len(result["trace"]["widgets"]) == 1, result
        assert not result["trace"]["widgets"][0]["a2ui"]["warnings"], result
        assert result["trace"]["widgetGates"], result
        assert session.inspect()["lastRequest"], "Local request snapshot missing"
    finally:
        if session._runner:
            await session._runner.close()
    print("PASS: installed SDK, native ADK tool turn, staged A2UI v0.9/v1.0, no stale emission")


if __name__ == "__main__":
    asyncio.run(verify())
