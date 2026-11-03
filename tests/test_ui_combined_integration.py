"""Interactions between A's native composer/history and B's locale/evidence state."""

from copy import deepcopy

from app.agent.enhancement.contracts import TaskStatus
from tests.test_chat_integration import payload, task
from tests.test_streamlit_chat_integration import screen, send


def test_language_switch_preserves_pending_memory_and_reply_evidence(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task("order_feasibility", pieces=800, category="TOPS")),
        payload(updates=[{"task_id": "task-001", "requested_delivery_date": "2026-04-25"}]),
    )
    send(app, "Assess 800 TOPS")
    thread = app.session_state["threads"][0]
    messages = deepcopy(thread["messages"])
    session = deepcopy(thread["agent_session"])
    selected = thread["selected_reply_id"]
    app.selectbox(key="ui_language").set_value("zh").run()
    assert not app.exception
    assert thread["messages"] == messages
    assert thread["agent_session"] == session
    assert thread["selected_reply_id"] == selected
    assert "800 TOPS" in app.button(key="thread_1").label
    assert len(app.chat_input) == 1 and not app.text_area
    assert not executor.calls
    send(app, "The delivery date is 2026-04-25")
    assert thread["agent_session"].state.tasks[0].status == TaskStatus.COMPLETED
    assert len(executor.calls) == 1
    assert thread["selected_reply_id"] != selected
    app.button(key="evidence_" + selected).click().run()
    assert not app.exception
    assert thread["selected_reply_id"] == selected
    assert thread["agent_session"].state.tasks[0].status == TaskStatus.COMPLETED
