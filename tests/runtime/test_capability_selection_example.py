"""Keep the published Unified Capability Selection walkthrough executable."""
import asyncio

import pytest

from examples.capability_selection import run_case


@pytest.mark.parametrize(("query", "expect_workflow"), [
    ("Find devices with a good camera", True),
    ("Hello", False),
])
def test_selection_walkthrough(query, expect_workflow):
    asyncio.run(run_case(query, expect_workflow=expect_workflow))
