"""Locale views must preserve original replies and never query tools/models."""

from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from app.agent.contracts import AgentReply
from app.agent.orchestrator import AgentOrchestrator
from app.ui.localized_reply import display_origin, localized_result_text
from app.ui.view_models import guided_decision


@pytest.mark.parametrize(
    "workflow,values",
    [
        ("Order status", {"order_id": "ORD-057"}),
        ("Production health", {"stage": "ASSEMBLY"}),
        ("Morning briefing", {"top_n": 3}),
        (
            "Order feasibility",
            {"category": "TOPS", "pieces": 800, "requested_delivery_date": "2026-04-25"},
        ),
    ],
)
def test_locale_view_reuses_stored_results_without_mutation_or_execution(workflow, values):
    reply = AgentOrchestrator().handle(guided_decision(workflow, **values))
    before = deepcopy(reply)
    with patch.object(AgentOrchestrator, "handle", Mock(side_effect=AssertionError("No reruns"))):
        chinese = localized_result_text(reply, "zh")
        assert chinese
        assert any("\u4e00" <= c <= "\u9fff" for c in chinese)
        assert localized_result_text(reply, "en") is None
    assert reply == before


def test_screenshot_order_numbers_and_id_preserved_in_chinese_view():
    reply = AgentOrchestrator().handle(guided_decision("Order status", order_id="ORD-057"))
    chinese = localized_result_text(reply, "zh")
    assert "ORD-057" in chinese and "8" in chinese
    assert "已完成" in chinese
    assert "was completed" in reply.text
    assert reply.metadata["language"] == "en"


def test_incomplete_or_free_text_has_no_invented_translation():
    for reply in (
        AgentReply("An arbitrary English paragraph"),
        AgentReply(
            "Incomplete",
            metadata={"intent": "order_status", "structured_result": {"order_id": "A"}},
        ),
        AgentReply("Failure", metadata={"status": "failed"}),
    ):
        before = deepcopy(reply)
        assert localized_result_text(reply, "zh") is None
        assert reply == before


def test_origin_is_a_ui_label_and_follows_both_languages():
    assert (
        display_origin("Structured workflow · deterministic tools", "zh")
        == "快捷工作流 · 业务规则工具"
    )
    assert (
        display_origin("快捷工作流 · 业务规则工具", "en")
        == "Structured workflow · deterministic tools"
    )
