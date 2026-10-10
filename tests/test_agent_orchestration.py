"""Real-data end-to-end tests for the first deterministic B-agent iteration."""

from app.agent.intents import Intent, IntentDecision
from app.agent.orchestrator import AgentOrchestrator


def test_order_status_pipeline_uses_real_order_and_preserves_evidence():
    reply = AgentOrchestrator().handle(
        IntentDecision(Intent.ORDER_STATUS, {"order_id": "ORD-057"})
    )

    result = reply.metadata["structured_result"]
    assert result["judgement"] == "NORMAL"
    assert result["rule_version"] == "ORDER_RISK_V1"
    assert reply.evidence[0].reference == "ORD-057"
    assert "NORMAL" in reply.text


def test_production_normality_pipeline_uses_real_production_data():
    reply = AgentOrchestrator().handle(
        IntentDecision(Intent.PRODUCTION_NORMALITY, {"stage": "assembly"})
    )

    result = reply.metadata["structured_result"]
    assert result["judgement"] == "ABNORMAL"
    assert result["historical_baseline"] == 774.9
    assert result["recent_performance"] == 425.5
    assert result["deviation_pct"] == -45.1
    assert reply.evidence


def test_morning_briefing_pipeline_returns_ranked_real_findings():
    reply = AgentOrchestrator().handle(IntentDecision(Intent.MORNING_BRIEFING))

    findings = reply.metadata["structured_result"]["findings"]
    assert len(findings) == 5
    assert findings[0]["finding_id"] == "ORDER:ORD-002"
    assert "ORD-002" in reply.text
    assert reply.evidence


def test_feasibility_pipeline_preserves_conditional_result_and_assumptions():
    reply = AgentOrchestrator().handle(
        IntentDecision(
            Intent.ORDER_FEASIBILITY,
            {
                "category": "TOPS",
                "pieces": 800,
                "requested_delivery_date": "2026-04-25",
            },
        )
    )

    result = reply.metadata["structured_result"]
    assert result["estimate"] == "CONDITIONAL"
    assert result["working_days"] == 21
    assert reply.assumptions == result["assumptions"]
    assert "CONDITIONAL" in reply.text
    assert reply.evidence


def test_missing_slots_returns_clarification_without_running_pipeline():
    reply = AgentOrchestrator().handle(
        IntentDecision(Intent.ORDER_FEASIBILITY, {"pieces": 800})
    )

    assert reply.needs_clarification is True
    assert reply.metadata["missing_slots"] == ["category", "requested_delivery_date"]
    assert reply.evidence == []


def test_unknown_order_returns_a_warning_instead_of_guessing():
    reply = AgentOrchestrator().handle(
        IntentDecision(Intent.ORDER_STATUS, {"order_id": "ORD-999"})
    )

    assert reply.metadata["status"] == "empty"
    assert reply.warnings
    assert reply.evidence == []
    assert "ORD-999" in reply.text


def test_invalid_feasibility_request_becomes_explicit_failure():
    reply = AgentOrchestrator().handle(
        IntentDecision(
            Intent.ORDER_FEASIBILITY,
            {
                "category": "TOPS",
                "pieces": 800,
                "requested_delivery_date": "2026-03-31",
            },
        )
    )

    assert reply.metadata["status"] == "failed"
    assert reply.warnings
    assert "必须晚于 today" in reply.text


def test_boolean_piece_count_is_rejected_instead_of_becoming_one_piece():
    reply = AgentOrchestrator().handle(
        IntentDecision(
            Intent.ORDER_FEASIBILITY,
            {
                "category": "TOPS",
                "pieces": True,
                "requested_delivery_date": "2026-04-25",
            },
        )
    )

    assert reply.metadata["status"] == "failed"
    assert reply.warnings == ["pieces must be a positive integer"]


def test_unknown_intent_requests_clarification():
    reply = AgentOrchestrator().handle(IntentDecision(Intent.UNKNOWN))

    assert reply.needs_clarification is True
    assert reply.suggested_question
