"""Expanded workflow views follow locale without rewriting stored results."""

from copy import deepcopy

import pytest

from app.agent.orchestrator import AgentOrchestrator
from app.ui.view_models import guided_decision
from tests.test_ui_evidence_selection import execute
from tests.ui_events import analysis_copy_payloads

REAL_HANDLE = AgentOrchestrator.handle

pytest_plugins = ["tests.test_ui_workflow_language"]


@pytest.mark.parametrize("source,target", [("en", "zh"), ("zh", "en")])
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
def test_expanded_view_changes_language_and_preserves_original(
    screen, source, target, workflow, values
):
    app, guided, service = screen
    original = REAL_HANDLE(
        AgentOrchestrator(), guided_decision(workflow, language=source, **values)
    )
    expected = REAL_HANDLE(
        AgentOrchestrator(), guided_decision(workflow, language=target, **values)
    )
    guided.side_effect = None
    guided.return_value = original
    execute(app)
    thread = app.session_state["threads"][0]
    identity = thread["selected_reply_id"]
    snapshot = deepcopy(thread["messages"])
    app.selectbox(key="ui_language").set_value(source).run()
    app.button(key="full_" + identity).click().run()
    assert any(item.value == original.text for item in app.markdown)
    app.selectbox(key="ui_language").set_value(target).run()
    assert not app.exception
    assert any(item.value == expected.text for item in app.markdown)
    assert analysis_copy_payloads(app) == {identity: {"text": expected.text, "language": target}}
    assert thread["messages"] == snapshot
    assert thread["expanded_answers"][identity]
    assert thread["selected_reply_id"] == identity
    app.selectbox(key="ui_language").set_value(source).run()
    assert any(item.value == original.text for item in app.markdown)
    assert guided.call_count == 1
    service.assert_not_called()
