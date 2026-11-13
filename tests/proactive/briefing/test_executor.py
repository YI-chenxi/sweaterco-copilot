"""Tests for the existing morning briefing workflow adapter."""

from datetime import date
from unittest.mock import patch

import pytest

from app.agent.contracts import AgentReply, Evidence
from app.agent.proactive.briefing.executor import (
    BriefingExecutionError,
    generate_morning_briefing,
)

REFERENCE_DATE = date(2026, 11, 4)


def test_generate_briefing_preserves_evidence_and_warnings():
    """A successful reply retains all traceable information."""
    reply = AgentReply(
        text="There are 2 priority items.",
        evidence=[
            Evidence(
                source="orders",
                reference="ORD-001",
                detail="Delivery risk",
            )
        ],
        warnings=["Data snapshot may be outdated."],
        assumptions=["Current production capacity remains unchanged."],
        metadata={
            "intent": "morning_briefing",
            "structured_result": {"findings": [{"severity_score": 80}]},
        },
    )

    with patch(
        "app.agent.proactive.briefing.executor.AgentOrchestrator"
    ) as mock_orchestrator:
        mock_orchestrator.return_value.handle.return_value = reply

        result = generate_morning_briefing(REFERENCE_DATE)

        settings = mock_orchestrator.call_args.kwargs["settings"]
        assert settings.reference_date == REFERENCE_DATE

    assert result["text"] == reply.text
    assert result["evidence"][0]["reference"] == "ORD-001"
    assert result["warnings"] == ["Data snapshot may be outdated."]
    assert result["assumptions"] == reply.assumptions
    assert result["metadata"]["structured_result"]["findings"][0]["severity_score"] == 80
    assert result["reference_date"] == "2026-11-04"


def test_generate_briefing_rejects_failed_reply():
    """A failed AgentReply must not be recorded as successful."""
    reply = AgentReply(
        text="Sorry, I couldn't complete that request.",
        warnings=["Tool execution failed."],
        metadata={
            "intent": "morning_briefing",
            "status": "failed",
        },
    )

    with patch(
        "app.agent.proactive.briefing.executor.AgentOrchestrator"
    ) as mock_orchestrator:
        mock_orchestrator.return_value.handle.return_value = reply

        with pytest.raises(BriefingExecutionError, match="generation failed"):
            generate_morning_briefing(REFERENCE_DATE)
