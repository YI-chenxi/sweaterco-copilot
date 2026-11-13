"""Real Streamlit pages + real Backend A/B SQLite services. No model/network calls."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.agent.intent_parser import IntentParser
from app.agent.llm_client import MockLLMClient
from app.agent.orchestrator import AgentOrchestrator
from app.agent.proactive.integration import build_proactive_application
from app.agent.service import AgentService
from app.agent.settings import LLMSettings

APP = Path(__file__).parents[2] / "app/ui/streamlit_app.py"


@pytest.fixture
def screen(monkeypatch, tmp_path):
    client = MockLLMClient([])
    application = build_proactive_application(
        llm_client=client,
        actor_provider=lambda: "operator",
        workspace_provider=lambda: "workspace",
        authorizer=lambda *args: True,
        database=tmp_path / "proactive.sqlite3",
    )
    agent = AgentService(
        IntentParser(client), AgentOrchestrator(), proactive_application=application
    )
    st.cache_resource.clear()
    monkeypatch.setattr(AgentService, "from_env", classmethod(lambda cls: agent))
    monkeypatch.setattr(
        LLMSettings, "from_env", classmethod(lambda cls: LLMSettings(provider="mock"))
    )
    app = AppTest.from_file(str(APP)).run(timeout=25)
    assert not app.exception
    yield app, application
    st.cache_resource.clear()


def click(app, key):
    app.button(key=key).click().run(timeout=25)
    assert not app.exception


def confirmed_watch(app, application):
    click(app, "pa_nav_watches")
    assert app.expander[0].proto.expanded
    app.selectbox(key="pa_field_watch_condition").set_value("status_equals").run(timeout=25)
    click(app, "pa_submit_watch_create")
    assert application.list_watches()["data"]["items"] == []
    assert len(application.list_pending_actions()["data"]["items"]) == 1
    identifier = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_confirm_" + identifier)
    return identifier, application.list_watches()["data"]["items"][0]["id"]


def test_watch_creation_requires_review_and_one_explicit_confirm(screen):
    app, application = screen
    identifier, _ = confirmed_watch(app, application)
    assert len(application.list_watches()["data"]["items"]) == 1
    assert not any(b.key == "pa_decide_confirm_" + identifier for b in app.button)
    assert app.button(key="pa_return_result").label == "View watch"
    app.run(timeout=25)
    assert len(application.list_watches()["data"]["items"]) == 1
    assert "idempotency_key" not in " ".join(m.value for m in app.markdown)


def test_cancel_watch_can_be_declined_and_history_counts_operations(screen):
    app, application = screen
    _, watch = confirmed_watch(app, application)
    click(app, "pa_nav_watches")
    click(app, "pa_cancel_watch_" + watch)
    assert len(application.list_watches()["data"]["items"]) == 1
    cancel = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_reject_" + cancel)
    assert len(application.list_watches()["data"]["items"]) == 1
    assert app.metric[1].value == "2"
    history = application.list_action_history()["data"]["items"]
    assert len(history) == 2 and sum(len(h["events"]) for h in history) > 2


def test_schedule_and_pause_both_wait_for_confirmation(screen):
    app, application = screen
    click(app, "pa_nav_briefings")
    app.radio(key="pa_field_briefings_section").set_value("Schedules").run(timeout=25)
    assert app.expander[0].proto.expanded
    click(app, "pa_submit_schedule_create")
    assert application.list_briefings()["data"]["items"] == []
    identifier = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_confirm_" + identifier)
    schedule = application.list_briefings()["data"]["items"][0]
    click(app, "pa_nav_briefings")
    click(app, "pa_toggle_" + schedule["id"])
    assert application.list_briefings()["data"]["items"][0]["enabled"] is True
    pause = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_confirm_" + pause)
    assert application.list_briefings()["data"]["items"][0]["enabled"] is False


def test_draft_survives_navigation_theme_and_language_without_proposing(screen):
    app, application = screen
    click(app, "pa_nav_watches")
    app.text_input(key="pa_field_watch_order").set_value("ORD-058").run(timeout=25)
    click(app, "pa_nav_briefings")
    click(app, "theme_night")
    app.selectbox(key="ui_language").set_value("zh").run(timeout=25)
    click(app, "pa_nav_watches")
    assert app.text_input(key="pa_field_watch_order").value == "ORD-058"
    assert application.list_pending_actions()["data"]["items"] == []
    assert app.button(key="pa_submit_watch_create").label == "提交待确认请求"


def test_read_error_has_no_fake_empty_state_or_stale_evidence(screen, monkeypatch):
    app, application = screen

    def fail():
        raise RuntimeError("do not reveal this")

    monkeypatch.setattr(application, "list_watches", fail)
    click(app, "pa_nav_watches")
    assert app.warning and not any(i.value == "No active watches." for i in app.info)
    assert all("do not reveal" not in item.value for item in app.warning)


def test_recent_chat_navigation_and_new_chat_restore_copilot(screen):
    app, _ = screen
    click(app, "pa_nav_actions")
    assert len(app.chat_input) == 0
    click(app, "new_chat")
    assert app.session_state["ui_page"] == "copilot" and len(app.chat_input) == 1


def test_chat_lists_saved_briefing_schedules(screen, monkeypatch):
    from app.agent.proactive.understanding.parser import ProactiveCommand

    _, application = screen
    created = application.briefings.create_schedule("workspace", {
        "interval_days": 1, "local_time": "09:00", "timezone": "Asia/Singapore",
        "start_date": "2026-04-02", "enabled": True,
    })
    assert created["ok"]
    monkeypatch.setattr(
        application.parser, "parse",
        lambda *_args, **_kwargs: ProactiveCommand(command_type="list", task_type="briefing"),
    )
    result = application.handle_text("List my briefing schedules.")
    assert result["command"]["task_type"] == "briefing"

    def page(entries):
        from app.ui.proactive.pages import render_chat_result

        render_chat_result({
            "ok": True, "status": "listed", "command": {"task_type": "briefing"},
            "data": {"items": entries},
        }, "reply")

    entries = result["data"]["items"]
    app = AppTest.from_function(page, args=(entries,)).run(timeout=25)
    assert not app.exception
    rendered = " ".join(item.value for item in app.markdown)
    assert "Briefing schedules" in rendered
    assert created["id"] in rendered


def test_generated_briefing_is_read_only_and_not_added_to_schedule_history(screen):
    app, application = screen
    click(app, "pa_nav_briefings")
    click(app, "pa_instant")
    assert app.session_state["proactive_ui"]["manual"]["reply"].evidence
    assert application.list_briefings()["data"]["history"] == []
    assert application.list_pending_actions()["data"]["items"] == []
