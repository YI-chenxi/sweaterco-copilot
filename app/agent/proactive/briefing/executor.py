"""Adapter for the existing deterministic morning briefing workflow."""

from dataclasses import asdict
from datetime import date
from typing import Any

from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.orchestrator import AgentOrchestrator
from app.agent.settings import AgentSettings


class BriefingExecutionError(Exception):
    """Raised when the existing briefing workflow reports failure."""


def generate_morning_briefing(
    reference_date: date,
    *,
    top_n: int = 5,
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> dict[str, Any]:
    """Run the existing morning briefing without an LLM."""

    orchestrator = AgentOrchestrator(
        settings=AgentSettings(
            reference_date=reference_date,
            briefing_top_n=top_n,
        )
    )

    decision = IntentDecision(
        intent=Intent.MORNING_BRIEFING,
        slots={},
        language=language,
    )

    reply = orchestrator.handle(decision)

    if reply.metadata.get("status") == "failed":
        raise BriefingExecutionError("Morning briefing generation failed.")

    if reply.needs_clarification:
        raise BriefingExecutionError("Morning briefing requires clarification.")

    return {
        "text": reply.text,
        "evidence": [asdict(item) for item in reply.evidence],
        "warnings": list(reply.warnings),
        "assumptions": list(reply.assumptions),
        "metadata": dict(reply.metadata),
        "reference_date": reference_date.isoformat(),
    }
