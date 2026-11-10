"""Real proposals stay pending until reviewed; returning never replays a write."""

import pytest

from tests.ui_events import dismiss_workflow_dialog
from tests.ui_proactive.test_pages import click, confirmed_watch
from tests.ui_proactive.test_pages import screen as screen


def prepare_watch(app):
    click(app, "pa_nav_watches")
    app.selectbox(key="pa_field_watch_condition").set_value("status_equals").run(timeout=25)
    click(app, "pa_submit_watch_create")
    return app.session_state["proactive_ui"].get("selected_action")


@pytest.mark.parametrize("dismiss", ("later", "close"))
def test_dismissal_preserves_pending_request_without_modal_reappearing(screen, dismiss):
    app, application = screen
    identifier = prepare_watch(app)
    assert app.get("dialog")
    assert app.button(key="pa_nav_actions").label == "Actions · 1"
    if dismiss == "later":
        click(app, "pa_review_later")
    else:
        dismiss_workflow_dialog(app)
    app.run(timeout=25)
    assert not app.get("dialog")
    assert application.list_watches()["data"]["items"] == []
    assert application.list_pending_actions()["data"]["items"][0]["id"] == identifier
    click(app, "theme_night")
    app.selectbox(key="ui_language").set_value("zh").run(timeout=25)
    assert not app.get("dialog")
    click(app, "pa_notice_review")
    assert app.session_state["ui_page"] == "actions"
    assert app.session_state["proactive_ui"]["selected_action"] == identifier


def test_back_before_confirmation_restores_draft_and_keeps_request_pending(screen):
    app, application = screen
    identifier = prepare_watch(app)
    click(app, "pa_notice_review")
    assert app.button(key="pa_decide_confirm_" + identifier).label == "Confirm & create watch"
    assert not app.get("dialog")
    click(app, "pa_back_source")
    assert app.session_state["ui_page"] == "watches"
    assert app.selectbox(key="pa_field_watch_condition").value == "status_equals"
    assert app.text_input(key="pa_field_watch_order").value == "ORD-057"
    assert len(application.list_pending_actions()["data"]["items"]) == 1
    assert application.list_watches()["data"]["items"] == []


def test_success_return_focuses_created_watch_without_resubmitting(screen):
    app, application = screen
    _, watch = confirmed_watch(app, application)
    assert any("Watch created successfully" in heading.value for heading in app.markdown)
    assert app.button(key="pa_nav_actions").label == "Actions"
    click(app, "pa_return_result")
    assert app.session_state["ui_page"] == "watches"
    assert app.radio(key="pa_field_watches_section").value == "Active watches"
    assert app.session_state["proactive_ui"]["return_target"]["id"] == watch
    assert not app.expander[0].proto.expanded
    app.run(timeout=25)
    assert len(application.list_watches()["data"]["items"]) == 1
    assert not app.get("dialog")


def test_declined_pause_returns_to_schedules_without_changing_enabled_state(screen):
    app, application = screen
    click(app, "pa_nav_briefings")
    app.radio(key="pa_field_briefings_section").set_value("Schedules").run(timeout=25)
    click(app, "pa_submit_schedule_create")
    identifier = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_confirm_" + identifier)
    click(app, "pa_return_result")
    schedule = application.list_briefings()["data"]["items"][0]
    click(app, "pa_toggle_" + schedule["id"])
    pause = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    assert app.button(key="pa_decide_confirm_" + pause).label == "Confirm pause"
    assert app.button(key="pa_decide_reject_" + pause).label == "Decline request"
    click(app, "pa_decide_reject_" + pause)
    assert any("Request declined" in h.value for h in app.markdown)
    click(app, "pa_return_result")
    assert app.radio(key="pa_field_briefings_section").value == "Schedules"
    assert application.list_briefings()["data"]["items"][0]["enabled"] is True


def test_unknown_creation_has_no_success_modal_and_cannot_be_blindly_submitted(screen, monkeypatch):
    app, application = screen
    original = application.propose_action
    calls = []

    def lose_acknowledgement(command):
        calls.append(command)
        original(command)
        raise RuntimeError("private error")

    monkeypatch.setattr(application, "propose_action", lose_acknowledgement)
    prepare_watch(app)
    assert not app.get("dialog")
    assert app.button(key="pa_submit_watch_create").disabled
    assert app.warning and "private error" not in " ".join(w.value for w in app.warning)
    app.run(timeout=25)
    assert len(calls) == 1
    assert len(application.list_pending_actions()["data"]["items"]) == 1
    assert application.list_watches()["data"]["items"] == []


def test_lost_confirmation_acknowledgement_uses_fresh_receipt_without_reexecution(
    screen, monkeypatch
):
    app, application = screen
    identifier = prepare_watch(app)
    click(app, "pa_notice_review")
    original = application.confirm_action
    calls = []

    def lose_acknowledgement(identifier):
        calls.append(identifier)
        original(identifier)
        raise RuntimeError("private error")

    monkeypatch.setattr(application, "confirm_action", lose_acknowledgement)
    click(app, "pa_decide_confirm_" + identifier)
    assert app.button(key="pa_return_result").label == "View watch"
    app.run(timeout=25)
    assert calls == [identifier]
    assert len(application.list_watches()["data"]["items"]) == 1


def test_chat_proposal_uses_same_modal_and_can_return_to_original_conversation(screen, monkeypatch):
    from app.agent.contracts import AgentReply
    from app.agent.service import AgentService
    from app.agent.settings import LLMSettings
    from app.agent.turn_service import TurnResult
    from app.ui.proactive.state import command

    app, application = screen
    calls = []

    def chat(self, text, session):
        calls.append(text)
        result = application.propose_action(
            command(
                "watch",
                "create",
                {
                    "order_id": "ORD-057",
                    "condition_type": "status_equals",
                    "expected_status": "COMPLETE",
                    "check_interval_minutes": 60,
                },
            )
        )
        return TurnResult(
            AgentReply("Please review this request.", metadata={"proactive": result}), session
        )

    monkeypatch.setattr(AgentService, "handle_turn", chat)
    monkeypatch.setattr(
        LLMSettings,
        "from_env",
        classmethod(lambda cls: LLMSettings(provider="openai", api_key="offline-ui-test")),
    )
    app.chat_input(key="chat_prompt").set_value("Watch ORD-057").run(timeout=25)
    assert not app.exception and app.get("dialog")
    identifier = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_confirm_" + identifier)
    click(app, "pa_back_source")
    assert app.session_state["ui_page"] == "copilot" and len(app.chat_input) == 1
    assert len(app.session_state["threads"][0]["messages"]) == 2
    assert calls == ["Watch ORD-057"]
    assert not app.get("dialog")
    assert any(b.label == "View in Actions" for b in app.button)


def test_unknown_outcome_and_cancellation_do_not_focus_a_nonexistent_result():
    from app.ui.proactive.state import destination, outcome_copy

    item = {
        "command": {"task_type": "watch", "command_type": "create", "parameters": {}},
        "status": "executed",
        "requires_reconciliation": True,
        "result": {"receipt": {"ok": True, "id": "watch"}},
    }
    assert destination(item)["id"] is None
    assert outcome_copy(item)[0] == "Outcome needs verification"
    item["requires_reconciliation"] = False
    item["command"]["command_type"] = "cancel"
    assert destination(item)["id"] is None
