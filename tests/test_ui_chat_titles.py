"""Conversation summaries stay short, specific and independent of later turns."""

import unicodedata
from copy import deepcopy

from app.agent.contracts import AgentReply
from app.ui.chat_titles import TITLE_WIDTH, clean_request_text, compact_title, conversation_title


def exchange(question, intent="order_status", **data):
    return [
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "reply": AgentReply(
                text="A verified business result.",
                metadata={"intent": intent, "structured_result": data},
            ),
        },
    ]


def test_long_first_request_is_summarized_from_actual_business_result():
    messages = exchange(
        "Please check my order and explain " + "every detail " * 40, order_id="ORD-057"
    )
    before = deepcopy(messages)
    assert conversation_title(messages) == "ORD-057 order status"
    assert messages == before


def test_chinese_request_gets_a_short_readable_label():
    assert conversation_title(exchange("请查询订单状态", order_id="ORD-057")) == "ORD-057 订单状态"


def test_later_followup_does_not_rename_the_first_exchange():
    messages = exchange("Check ORD-057", order_id="ORD-057")
    messages += exchange("Now assess 800 TOPS", "order_feasibility", pieces=800, category="TOPS")
    assert conversation_title(messages) == "ORD-057 order status"


def test_pending_two_task_request_keeps_separate_parameters_in_title():
    reply = AgentReply(
        text="A date is needed.",
        metadata={
            "task_order": ["task-001", "task-002"],
            "tasks": {
                "task-001": {
                    "intent": "order_status",
                    "decision": {"slots": {"order_id": "ORD-057"}},
                },
                "task-002": {
                    "intent": "order_feasibility",
                    "decision": {"slots": {"pieces": 800, "category": "TOPS"}},
                },
            },
        },
    )
    messages = [
        {"role": "user", "content": "Check ORD-057 and assess 800 TOPS"},
        {"role": "assistant", "reply": reply},
    ]
    title = conversation_title(messages)
    assert "ORD-057" in title and "800 TOPS" in title
    assert "task-" not in title


def test_direct_help_response_uses_request_not_internal_metadata():
    messages = exchange("Please tell me what you can help with", "conversation")
    title = conversation_title(messages)
    assert title == "tell me what you can help with"
    assert "conversation" not in title


def test_structured_workflow_suffix_is_removed():
    messages = [{"role": "user", "content": "Order status · ORD-057  ·  Structured workflow"}]
    assert conversation_title(messages) == "Order status · ORD-057"


def test_long_chinese_and_english_labels_obey_visual_budget():
    for request in ["订单生产进展和产能情况" * 20, "Check the production progress " * 20]:
        title = compact_title(request)
        width = sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in title)
        assert width <= TITLE_WIDTH
        assert title.endswith("…")


def test_empty_conversation_does_not_reuse_another_title():
    assert conversation_title([]) == "New conversation"


def test_categories_and_stages_are_preserved_without_inventing_outcomes():
    assert (
        conversation_title(exchange("Check ASSEMBLY", "production_normality", stage="ASSEMBLY"))
        == "ASSEMBLY production check"
    )
    assert (
        conversation_title(
            exchange("Assess 800 TOPS", "order_feasibility", pieces=800, category="TOPS")
        )
        == "800 TOPS feasibility"
    )


def test_guided_feasibility_preserves_quantity_when_result_has_no_request_slots():
    messages = exchange(
        "Feasibility · 800 TOPS by 2026-04-25 · Structured workflow",
        "order_feasibility",
        estimate="SUPPORTED",
        working_days=21,
    )
    assert conversation_title(messages) == "800 TOPS feasibility"
    assert clean_request_text(messages[0]["content"]) == "Feasibility · 800 TOPS by 2026-04-25"


def test_origin_cleanup_keeps_dates_and_user_written_structured_workflow_questions():
    assert clean_request_text("What is a structured workflow?") == "What is a structured workflow?"
    assert clean_request_text("Feasibility · 900 TOPS by 2026-05-01") == (
        "Feasibility · 900 TOPS by 2026-05-01"
    )
