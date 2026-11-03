"""Replaceable structured-output clients for OpenAI and offline tests."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Protocol

from app.agent.settings import LLMSettings


class LLMClientError(RuntimeError):
    """Raised when a model call cannot produce a usable structured result."""


class StructuredLLMClient(Protocol):
    """Minimal interface required by the intent parser."""

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one object that conforms to the supplied JSON schema."""


class MockLLMClient:
    """Deterministic queued responses for tests and offline development."""

    def __init__(self, responses: Iterable[dict[str, Any] | Exception] = ()) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": schema,
            }
        )
        if not self._responses:
            raise LLMClientError("No mock LLM response was configured")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return dict(response)


class OpenAILLMClient:
    """OpenAI Responses API adapter using strict JSON Schema output."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        client: Any | None = None,
    ) -> None:
        self.model = model
        if client is not None:
            self._client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMClientError(
                "The openai package is not installed; install requirements.txt"
            ) from exc
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds)

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = self._client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "sweaterco_intent_decision",
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:
            root_cause = exc
            while root_cause.__cause__ is not None:
                root_cause = root_cause.__cause__
            detail = f"{type(exc).__name__}: {exc}"
            if root_cause is not exc:
                detail += f" ({type(root_cause).__name__}: {root_cause})"
            raise LLMClientError(f"OpenAI request failed: {detail}") from exc

        status = getattr(response, "status", None)
        if status not in (None, "completed"):
            raise LLMClientError(f"OpenAI response did not complete: {status}")
        output_text = getattr(response, "output_text", "")
        if not output_text:
            raise LLMClientError("OpenAI response contained no structured output")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise LLMClientError("OpenAI structured output was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise LLMClientError("OpenAI structured output must be a JSON object")
        return payload


def create_llm_client(settings: LLMSettings) -> StructuredLLMClient:
    """Create the configured provider without exposing credentials downstream."""

    if settings.provider == "openai":
        if not settings.api_key:
            raise LLMClientError("OPENAI_API_KEY is required for MODEL_PROVIDER=openai")
        return OpenAILLMClient(
            model=settings.model,
            api_key=settings.api_key,
            timeout_seconds=settings.timeout_seconds,
        )
    return MockLLMClient()
