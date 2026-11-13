"""Runtime settings owned by the B-agent orchestration layer."""

import os
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AgentSettings:
    """Configuration that must be injected rather than inferred by an LLM."""

    reference_date: date = date(2026, 4, 1)
    briefing_top_n: int = 5

    def __post_init__(self) -> None:
        if self.briefing_top_n <= 0:
            raise ValueError("briefing_top_n must be greater than 0")


@dataclass(frozen=True)
class LLMSettings:
    """Configuration for the replaceable language-model adapter."""

    provider: str = "mock"
    model: str = "gpt-6-luna"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        if provider not in {"mock", "openai"}:
            raise ValueError("MODEL_PROVIDER must be 'mock' or 'openai'")
        if not self.model.strip():
            raise ValueError("OPENAI_MODEL must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("OPENAI_TIMEOUT_SECONDS must be greater than 0")
        object.__setattr__(self, "provider", provider)

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "LLMSettings":
        """Load local settings without logging or exposing the API key."""

        if load_dotenv_file:
            from dotenv import load_dotenv

            load_dotenv()
        timeout_value = os.getenv("OPENAI_TIMEOUT_SECONDS", "30")
        try:
            timeout_seconds = float(timeout_value)
        except ValueError as exc:
            raise ValueError("OPENAI_TIMEOUT_SECONDS must be numeric") from exc
        return cls(
            provider=os.getenv("MODEL_PROVIDER", "mock"),
            model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
            api_key=os.getenv("OPENAI_API_KEY") or None,
            timeout_seconds=timeout_seconds,
        )
