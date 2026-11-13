"""Tests for the provider-neutral and OpenAI structured-output clients."""

import json
from types import SimpleNamespace

import pytest

from app.agent.llm_client import LLMClientError, MockLLMClient, OpenAILLMClient


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeOpenAI:
    def __init__(self, response):
        self.responses = FakeResponses(response)


def test_mock_client_records_prompts_and_returns_a_copy():
    payload = {"intent": "morning_briefing"}
    client = MockLLMClient([payload])

    result = client.generate_structured(
        system_prompt="system",
        user_prompt="brief me",
        schema={"type": "object"},
    )

    assert result == payload
    assert result is not payload
    assert client.calls[0]["user_prompt"] == "brief me"


def test_openai_client_uses_responses_api_strict_json_schema():
    payload = {"intent": "morning_briefing", "language": "en"}
    fake = FakeOpenAI(
        SimpleNamespace(status="completed", output_text=json.dumps(payload))
    )
    client = OpenAILLMClient(model="test-model", client=fake)
    schema = {"type": "object", "additionalProperties": False}

    result = client.generate_structured(
        system_prompt="system",
        user_prompt="What needs attention?",
        schema=schema,
    )

    assert result == payload
    call = fake.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["input"][0]["role"] == "system"
    assert call["input"][1]["role"] == "user"
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"] == schema


def test_openai_client_rejects_incomplete_responses():
    fake = FakeOpenAI(SimpleNamespace(status="incomplete", output_text=""))
    client = OpenAILLMClient(model="test-model", client=fake)

    with pytest.raises(LLMClientError, match="did not complete"):
        client.generate_structured(
            system_prompt="system",
            user_prompt="question",
            schema={"type": "object"},
        )


def test_openai_client_rejects_invalid_json():
    fake = FakeOpenAI(SimpleNamespace(status="completed", output_text="not-json"))
    client = OpenAILLMClient(model="test-model", client=fake)

    with pytest.raises(LLMClientError, match="not valid JSON"):
        client.generate_structured(
            system_prompt="system",
            user_prompt="question",
            schema={"type": "object"},
        )
