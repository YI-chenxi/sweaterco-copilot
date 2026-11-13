"""Streamlit-independent tests for our UI adaptation to the real B contract."""

import csv
import io

import pytest

from app.agent.contracts import AgentReply, Evidence
from app.agent.intents import Intent, ResponseLanguage
from app.agent.orchestrator import AgentOrchestrator
from app.ui.view_models import (
    evidence_csv,
    evidence_rows,
    findings_for_reply,
    guided_decision,
    metrics_for_reply,
)


def test_evidence_export_preserves_complete_source_rows():
    reply = AgentReply(
        text="Sample",
        evidence=[
            Evidence("orders.csv", "ORD-057", 'Escaped, "quoted" detail'),
            Evidence("production_log.csv", "ASSEMBLY", "Second row"),
        ],
    )
    assert len(evidence_rows(reply)) == 2
    exported = list(csv.DictReader(io.StringIO(evidence_csv(reply))))
    assert exported[0]["Detail"] == 'Escaped, "quoted" detail'
    assert len(exported) == 2


def test_no_fabricated_metrics_when_structured_result_is_absent():
    reply = AgentReply(text="Could not determine", metadata={"intent": "unknown"})
    assert metrics_for_reply(reply) == []
    assert findings_for_reply(reply) == []


@pytest.mark.parametrize(
    ("workflow", "intent"),
    [
        ("Order status", Intent.ORDER_STATUS),
        ("Production health", Intent.PRODUCTION_NORMALITY),
        ("Morning briefing", Intent.MORNING_BRIEFING),
        ("Order feasibility", Intent.ORDER_FEASIBILITY),
    ],
)
def test_guided_decision_correctly_routes_to_existing_intent(workflow, intent):
    decision = guided_decision(workflow, language="zh", order_id="ORD-057")
    assert decision.intent == intent
    assert decision.language == ResponseLanguage.CHINESE


def test_guided_order_status_uses_real_backend_data():
    decision = guided_decision("Order status", order_id="ORD-057")
    reply = AgentOrchestrator().handle(decision)
    assert reply.metadata["structured_result"]["order_id"] == "ORD-057"
    assert reply.evidence and reply.evidence[0].source == "orders.csv"
    assert metrics_for_reply(reply)[0].value == "ORD-057"


def test_guided_production_uses_actual_judgement():
    decision = guided_decision("Production health", stage="ASSEMBLY")
    reply = AgentOrchestrator().handle(decision)
    assert reply.metadata["structured_result"]["judgement"] in {"NORMAL", "ABNORMAL"}
    assert reply.evidence
    assert any(item.label == "Deviation" for item in metrics_for_reply(reply))


def test_guided_briefing_returns_ranked_findings():
    decision = guided_decision("Morning briefing", top_n=3)
    reply = AgentOrchestrator().handle(decision)
    assert len(findings_for_reply(reply)) == 3
    assert reply.evidence


def test_guided_feasibility_preserves_assumptions():
    decision = guided_decision(
        "Order feasibility", category="TOPS", pieces=800,
        requested_delivery_date="2026-04-25",
    )
    reply = AgentOrchestrator().handle(decision)
    assert reply.metadata["structured_result"]["estimate"]
    assert len(reply.assumptions) >= 1
    assert reply.evidence


def test_invalid_workflow_rejected_safely():
    with pytest.raises(ValueError, match="Unknown guided workflow"):
        guided_decision("Unknown")
