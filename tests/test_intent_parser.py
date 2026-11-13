"""Tests for bilingual natural-language intent extraction and validation."""

from app.agent.intent_parser import IntentParser, decision_from_payload
from app.agent.intents import Intent, ResponseLanguage
from app.agent.llm_client import LLMClientError, MockLLMClient


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


def test_parser_extracts_and_normalizes_english_order_status():
    client = MockLLMClient(
        [payload(intent="order_status", order_id=" ord-057 ")]
    )

    decision = IntentParser(client).parse("How is order ORD-057?")

    assert decision.intent == Intent.ORDER_STATUS
    assert decision.language == ResponseLanguage.ENGLISH
    assert decision.slots == {"order_id": "ORD-057"}


def test_parser_supports_chinese_production_question():
    client = MockLLMClient(
        [payload(intent="production_normality", language="zh", stage="assembly")]
    )

    decision = IntentParser(client).parse("Assembly 最近正常吗？")

    assert decision.intent == Intent.PRODUCTION_NORMALITY
    assert decision.language == ResponseLanguage.CHINESE
    assert decision.slots == {"stage": "ASSEMBLY"}


def test_parser_preserves_missing_feasibility_category():
    client = MockLLMClient(
        [
            payload(
                intent="order_feasibility",
                pieces=800,
                requested_delivery_date="2026-04-25",
                missing_slots=["category"],
            )
        ]
    )

    decision = IntentParser(client).parse(
        "Can we accept 800 hoodies by 2026-04-25?"
    )

    assert decision.intent == Intent.ORDER_FEASIBILITY
    assert decision.slots["pieces"] == 800
    assert decision.missing_slots == ("category",)


def test_invalid_intent_becomes_unknown_and_drops_slots():
    decision = decision_from_payload(
        payload(intent="invented_tool", order_id="ORD-057"),
        fallback_language=ResponseLanguage.ENGLISH,
    )

    assert decision.intent == Intent.UNKNOWN
    assert decision.slots == {}


def test_model_failure_falls_back_to_unknown_in_user_language():
    client = MockLLMClient([LLMClientError("network unavailable")])

    decision = IntentParser(client).parse("请检查订单 ORD-057")

    assert decision.intent == Intent.UNKNOWN
    assert decision.language == ResponseLanguage.CHINESE


def test_empty_input_does_not_call_model():
    client = MockLLMClient()

    decision = IntentParser(client).parse("  ")

    assert decision.intent == Intent.UNKNOWN
    assert client.calls == []
