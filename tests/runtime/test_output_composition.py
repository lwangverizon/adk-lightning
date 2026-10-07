"""A host replacement must not hide explicitly installed output transforms."""
import asyncio

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adkl.bindings import RuntimeBindings


def test_host_response_replacement_then_redaction():
    replacement = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="123-45-6789")]))
    def host(callback_context, llm_response):
        return replacement
    binding = RuntimeBindings(skills=[], feature_config={"pipeline": ["pii_redaction"],
                                                       "pii_redaction": {"kinds": ["ssn"]}})
    kwargs = binding.agent_kwargs(callbacks={"after_model_callback": [host]})
    result = asyncio.run(kwargs["after_model_callback"][0](None, LlmResponse()))
    assert result is replacement
    assert result.content.parts[0].text == "[ssn]"


def test_custom_output_transform_errors_propagate():
    def broken(callback_context, llm_response):
        raise ValueError("failed filter")
    binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
    kwargs = binding.agent_kwargs(after_model_transforms=[broken])
    with pytest.raises(ValueError, match="failed filter"):
        asyncio.run(kwargs["after_model_callback"][0](None, LlmResponse()))
