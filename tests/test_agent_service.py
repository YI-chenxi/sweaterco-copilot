"""Natural-language end-to-end tests using a mock LLM and real A1/A2 tools."""

from app.agent.intent_parser import IntentParser
from app.agent.llm_client import MockLLMClient
from app.agent.orchestrator import AgentOrchestrator
from app.agent.service import AgentService


def payload(**overrides):
    result = {
        "intent": "unknown",
        "language": "en",
        "order_id": None,
        "stage": None,
        "category": None,
        "pieces": None,
        "requested_delivery_date": None,
        "top_n": None,
        "missing_slots": [],
    }
    result.update(overrides)
    return result


def service_for(response):
    return AgentService(
        parser=IntentParser(MockLLMClient([response])),
        orchestrator=AgentOrchestrator(),
    )


def test_english_message_runs_real_order_pipeline_and_replies_in_english():
    service = service_for(payload(intent="order_status", order_id="ORD-057"))

    reply = service.handle_text("How is ORD-057?")

    assert reply.metadata["structured_result"]["judgement"] == "NORMAL"
    assert reply.metadata["language"] == "en"
    assert reply.text.startswith("**Order ORD-057")
    assert "The due date passed 58 days ago" in reply.text
    assert "the last recorded activity was 66 days ago" in reply.text
    assert reply.evidence[0].reference == "ORD-057"


def test_chinese_message_runs_real_normality_pipeline_and_replies_in_chinese():
    service = service_for(
        payload(
            intent="production_normality",
            language="zh",
            stage="ASSEMBLY",
        )
    )

    reply = service.handle_text("Assembly 最近正常吗？")

    assert reply.metadata["structured_result"]["judgement"] == "ABNORMAL"
    assert reply.metadata["language"] == "zh"
    assert "偏离正常生产水平" in reply.text
    assert "ABNORMAL" not in reply.text


def test_english_missing_slot_returns_english_clarification():
    service = service_for(
        payload(
            intent="order_feasibility",
            pieces=800,
            requested_delivery_date="2026-04-25",
            missing_slots=["category"],
        )
    )

    reply = service.handle_text("Can we accept 800 hoodies by April 25?")

    assert reply.needs_clarification is True
    assert "product category" in reply.text
    assert reply.metadata["language"] == "en"


def test_chinese_feasibility_assumptions_are_preserved_in_chinese():
    service = service_for(
        payload(
            intent="order_feasibility",
            language="zh",
            category="TOPS",
            pieces=800,
            requested_delivery_date="2026-04-25",
        )
    )

    reply = service.handle_text("800 件 TOPS，4 月 25 日能不能接？")

    assert reply.metadata["structured_result"]["estimate"] == "CONDITIONAL"
    assert reply.assumptions[0] == "Sunday（周日）不计生产日"
    assert "有条件可行" in reply.text
    assert "CONDITIONAL" not in reply.text
