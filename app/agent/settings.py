"""Runtime settings owned by the B-agent orchestration layer."""

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
