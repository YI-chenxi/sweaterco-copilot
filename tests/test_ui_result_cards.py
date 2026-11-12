"""Unified cards preserve and immediately display full original payloads."""

from copy import deepcopy

from app.ui.result_cards import card_summary
from tests.test_ui_evidence_selection import execute
from tests.ui_events import analysis_copy_payloads

pytest_plugins = ["tests.test_ui_workflow_language"]


def test_each_answer_is_complete_without_an_expansion_step_or_execution(screen):
    app, guided, service = screen
    execute(app)
    execute(app)
    thread = app.session_state["threads"][0]
    snapshot = deepcopy(thread["messages"])
    ids = [m["ui_reply_id"] for m in thread["messages"] if m["role"] == "assistant"]
    assert not any(
        button.key.endswith("full_" + identity) for button in app.button for identity in ids
    )
    assert all(
        any(markdown.value == snapshot[index]["reply"].text for markdown in app.markdown)
        for index in (1, 3)
    )
    assert not any("Supporting Details" in markdown.value for markdown in app.markdown)
    assert analysis_copy_payloads(app) == {
        ids[0]: {"text": snapshot[1]["reply"].text, "language": "en"},
        ids[1]: {"text": snapshot[3]["reply"].text, "language": "en"},
    }
    assert not app.code and not app.get("popover")
    assert thread["messages"] == snapshot
    assert guided.call_count == 2
    service.assert_not_called()


def test_all_four_summaries_use_actual_backend_results():
    from app.agent.orchestrator import AgentOrchestrator
    from app.ui.view_models import guided_decision

    cases = [
        ("Order status", {"order_id": "ORD-057"}),
        ("Production health", {"stage": "ASSEMBLY"}),
        ("Morning briefing", {"top_n": 3}),
        (
            "Order feasibility",
            {"category": "TOPS", "pieces": 800, "requested_delivery_date": "2026-04-25"},
        ),
    ]
    for name, values in cases:
        reply = AgentOrchestrator().handle(guided_decision(name, **values))
        original = deepcopy(reply)
        _, _, tone, conclusion, metrics = card_summary(reply, "en")
        assert conclusion
        assert "\n" not in conclusion
        assert metrics
        assert reply == original
        if name == "Morning briefing":
            assert len(metrics) == 1
            assert metrics[0].value == "3"
        if name == "Order feasibility":
            assert tone == "watch"
            assert "external capacity" in conclusion
