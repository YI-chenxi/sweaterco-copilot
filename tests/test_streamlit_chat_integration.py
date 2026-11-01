"""Exercise the real Streamlit screen with offline queued model responses."""

from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from app.agent.enhancement.contracts import TaskStatus
from app.agent.service import AgentService
from app.agent.settings import LLMSettings
from tests.test_chat_integration import payload, service, task

APP = Path(__file__).resolve().parents[1] / "app/ui/streamlit_app.py"


def screen(monkeypatch, *responses):
    agent, client, executor = service(*responses)
    st.cache_resource.clear()
    monkeypatch.setattr(AgentService, "from_env", classmethod(lambda cls: agent))
    settings = LLMSettings(provider="openai", api_key="offline-ui-test")
    monkeypatch.setattr(LLMSettings, "from_env", classmethod(lambda cls: settings))
    app = AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception
    return app, client, executor


def send(app, message):
    app.text_area(key="draft_text").set_value(message)
    app.button(key="send_message").click().run(timeout=20)
    assert not app.exception


def test_ui_missing_date_followup_saves_final_state_and_source_history(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task(order_id="ORD-057"), task("order_feasibility", category="TOPS", pieces=800)),
        payload(updates=[{"task_id": "task-002", "requested_delivery_date": "2026-04-25"}]),
    )
    send(app, "Check ORD-057 and assess 800 TOPS")
    assert app.button(key="reset_chat_request")
    first_reply = app.session_state["threads"][0]["messages"][1]["reply"]
    assert first_reply.needs_clarification
    for internal_term in (
        "task-002",
        "PENDING",
        "planner",
        "order_feasibility",
        "requested_delivery_date",
    ):
        assert internal_term not in first_reply.text
    captions = " ".join(item.value for item in app.caption)
    assert "task-002" not in captions
    assert "delivery date" in captions
    send(app, "TOPS is due on 2026-04-25")
    thread = app.session_state["threads"][0]
    assert len(thread["messages"]) == 4
    assert all(task.status == TaskStatus.COMPLETED for task in thread["agent_session"].state.tasks)
    assert len(executor.calls) == 2
    assert thread["messages"][1]["reply"].evidence
    assert thread["messages"][3]["reply"].assumptions
    st.cache_resource.clear()


def test_ui_new_conversation_does_not_inherit_pending_slots(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        payload(task(order_id="ORD-057")),
    )
    send(app, "Assess 800 TOPS")
    first = app.session_state["threads"][0]
    app.button(key="new_chat").click().run(timeout=20)
    assert not app.exception
    send(app, "Check ORD-057")
    threads = app.session_state["threads"]
    assert len(threads) == 2
    assert threads[0]["agent_session"].state.tasks[0].decision.intent.value == "order_status"
    assert first["agent_session"].state.tasks[0].status == TaskStatus.PENDING
    assert len(executor.calls) == 1
    st.cache_resource.clear()


def test_ui_global_blocker_reset_and_two_task_details(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(issues=[{"issue_type": "too_many_tasks"}], ambiguous=True),
        payload(task("production_normality", stage="ASSEMBLY"), task(order_id="ORD-057")),
    )
    send(app, "Run three tasks")
    assert not executor.calls
    app.button(key="reset_chat_request").click().run(timeout=20)
    assert not app.exception
    assert not app.session_state["threads"][0]["agent_session"].global_issues
    send(app, "Check assembly and ORD-057")
    assert len(executor.calls) == 2
    assert len(app.metric) >= 4
    assert len(app.session_state["threads"][0]["messages"]) == 4
    st.cache_resource.clear()


def test_ui_morning_briefing_does_not_repeat_findings_as_a_table(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task("morning_briefing", top_n=4)),
    )

    send(app, "Show me the top four priorities")

    assert len(executor.calls) == 1
    assert not any(expander.label.startswith("Priority findings") for expander in app.expander)
    assert not any("sc-answer-banner" in item.value for item in app.markdown)
    assert not any("sc-next-step" in item.value for item in app.markdown)
    assert not any("Review evidence before acting" in item.value for item in app.markdown)
    st.cache_resource.clear()
