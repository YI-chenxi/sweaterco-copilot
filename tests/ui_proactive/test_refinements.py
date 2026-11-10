"""Regression coverage for reusable drafts, explicit resubmission and presentation."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.ui.proactive.presentation import readable_time
from tests.ui_proactive.test_pages import click, confirmed_watch
from tests.ui_proactive.test_pages import screen as screen


def test_returning_to_unused_chat_reuses_its_id(screen):
    app, _ = screen
    first = app.session_state["active_thread"]
    click(app, "pa_nav_briefings")
    click(app, "new_chat")
    assert app.session_state["active_thread"] == first
    assert len(app.session_state["threads"]) == 1
    click(app, "pa_nav_watches")
    click(app, "new_chat")
    assert len(app.session_state["threads"]) == 1


def test_rejected_create_can_be_created_again_without_a_reset_button(screen):
    app, application = screen
    click(app, "pa_nav_watches")
    app.selectbox(key="pa_field_watch_condition").set_value("status_equals").run(timeout=25)
    click(app, "pa_submit_watch_create")
    old = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_reject_" + old)
    click(app, "pa_return_result")
    click(app, "pa_submit_watch_create")
    new = app.session_state["proactive_ui"]["selected_action"]
    assert new != old and app.get("dialog")
    assert not any(b.key.startswith("pa_new_") for b in app.button if b.key)
    assert len(application.list_pending_actions()["data"]["items"]) == 1
    # Another explicit click while still pending must reuse this SAME request.
    click(app, "pa_review_later")
    click(app, "pa_submit_watch_create")
    assert app.session_state["proactive_ui"]["selected_action"] == new
    assert len(application.list_pending_actions()["data"]["items"]) == 1


def test_declined_cancel_can_be_requested_again(screen):
    app, application = screen
    _, watch = confirmed_watch(app, application)
    click(app, "pa_return_result")
    click(app, "pa_cancel_watch_" + watch)
    old = app.session_state["proactive_ui"]["selected_action"]
    click(app, "pa_notice_review")
    click(app, "pa_decide_reject_" + old)
    click(app, "pa_return_result")
    click(app, "pa_cancel_watch_" + watch)
    new = app.session_state["proactive_ui"]["selected_action"]
    assert new != old
    click(app, "pa_notice_review")
    click(app, "pa_decide_confirm_" + new)
    assert application.list_watches()["data"]["items"] == []


def test_blank_order_uses_small_error_dialog_and_does_not_submit(screen):
    app, application = screen
    click(app, "pa_nav_watches")
    app.text_input(key="pa_field_watch_order").set_value("").run(timeout=25)
    click(app, "pa_submit_watch_create")
    assert app.get("dialog")
    assert any("Enter an order number" in p.value for p in app.markdown)
    assert not app.warning
    assert application.list_pending_actions()["data"]["items"] == []
    click(app, "pa_error_return")
    assert not app.get("dialog")
    assert app.text_input(key="pa_field_watch_order").value == ""


@pytest.mark.parametrize("zone", ["Asia/Singapore", "Europe/London", "America/New_York"])
def test_all_display_timestamps_drop_iso_separator_microseconds_and_keep_local_zone(zone):
    raw = "2026-11-10T02:27:46.168620+00:00"
    expected = datetime.fromisoformat(raw).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M:%S")
    shown = readable_time(raw, zone)
    assert shown.startswith(expected)
    assert "T02" not in shown and ".168620" not in shown
