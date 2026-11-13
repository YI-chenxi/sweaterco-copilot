"""Focused wording tests for deterministic bilingual responses."""

from app.agent.contracts import ToolResult
from app.agent.intents import ResponseLanguage
from app.agent.response_builder import build_order_status_reply


def order_result_for_today() -> ToolResult:
    return ToolResult(
        data={
            "order_id": "ORD-TODAY",
            "judgement": "NORMAL",
            "labels": [],
            "derived_metrics": {
                "days_to_due": 0,
                "days_since_activity": 0,
            },
            "rule_version": "ORDER_RISK_V1",
        }
    )


def order_result_for_tomorrow() -> ToolResult:
    result = order_result_for_today()
    result.data["derived_metrics"] = {
        "days_to_due": 1,
        "days_since_activity": 1,
    }
    return result


def test_english_today_wording_avoids_zero_days_ago():
    reply = build_order_status_reply(order_result_for_today())

    assert "The order is due today" in reply.text
    assert "the last recorded activity was today" in reply.text
    assert "0 days ago" not in reply.text


def test_chinese_today_wording_is_natural():
    reply = build_order_status_reply(
        order_result_for_today(), ResponseLanguage.CHINESE
    )

    assert "今天到期" in reply.text
    assert "今天有活动记录" in reply.text


def test_english_single_day_wording_uses_singular_day():
    reply = build_order_status_reply(order_result_for_tomorrow())

    assert "due in 1 day" in reply.text
    assert "activity was 1 day ago" in reply.text
    assert "1 days" not in reply.text
