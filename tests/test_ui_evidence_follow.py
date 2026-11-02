"""Validate browser-selected answer IDs without business calls or mixed payloads."""

from copy import deepcopy

import pytest

from app.ui.evidence_follow import apply_follow_event
from tests.test_ui_evidence_selection import execute

pytest_plugins = ["tests.test_ui_workflow_language"]


def test_valid_event_changes_only_selection():
    thread = {
        "id": 1,
        "selected_reply_id": "b",
        "messages": [
            {"role": "assistant", "ui_reply_id": "a", "payload": ["evidence a"]},
            {"role": "assistant", "ui_reply_id": "b", "payload": ["evidence b"]},
        ],
    }
    before = deepcopy(thread["messages"])
    assert apply_follow_event(
        thread, {"thread": 1, "answer": "a"}, enabled=True, panel_open=True, active_thread=1
    )
    assert thread["selected_reply_id"] == "a"
    assert thread["messages"] == before
    assert not apply_follow_event(
        thread, {"thread": 1, "answer": "a"}, enabled=True, panel_open=True, active_thread=1
    )


@pytest.mark.parametrize(
    "event,enabled,opened,active",
    [
        ({"thread": 2, "answer": "a"}, True, True, 1),
        ({"thread": 1, "answer": "foreign"}, True, True, 1),
        ({"thread": 1, "answer": "a"}, False, True, 1),
        ({"thread": 1, "answer": "a"}, True, False, 1),
        ({"thread": 1, "answer": "a"}, True, True, 2),
        ({"thread": 1}, True, True, 1),
        (None, True, True, 1),
    ],
)
def test_stale_invalid_disabled_and_hidden_events_are_ignored(event, enabled, opened, active):
    thread = {
        "id": 1,
        "selected_reply_id": "b",
        "messages": [{"role": "assistant", "ui_reply_id": "a"}],
    }
    before = deepcopy(thread)
    assert not apply_follow_event(
        thread, event, enabled=enabled, panel_open=opened, active_thread=active
    )
    assert thread == before


def test_manual_evidence_preserves_follow_setting_and_history(screen):
    app, guided, service = screen
    execute(app)
    execute(app)
    thread = app.session_state["threads"][0]
    before = deepcopy(thread["messages"])
    identity = before[1]["ui_reply_id"]
    assert app.toggle(key="follow_evidence").value
    app.button(key="evidence_" + identity).click().run()
    assert not app.exception
    assert app.toggle(key="follow_evidence").value
    assert thread["selected_reply_id"] == identity
    app.run()
    assert thread["selected_reply_id"] == identity
    assert thread["messages"] == before
    assert guided.call_count == 2
    service.assert_not_called()


def test_older_streamlit_keeps_manual_evidence_usable(monkeypatch):
    from app.ui import evidence_follow

    def unavailable(name):
        raise ImportError("v2 unavailable")

    monkeypatch.setattr(evidence_follow, "import_module", unavailable)
    assert not evidence_follow.follow_supported()
    evidence_follow._component.clear()
    assert evidence_follow.mount_follow({"id": 1, "messages": []}, True, lambda: None) is None
    evidence_follow._component.clear()


def test_delayed_browser_events_from_previous_render_are_ignored():
    thread = {
        "id": 1,
        "follow_token": "new-render",
        "selected_reply_id": "b",
        "messages": [{"role": "assistant", "ui_reply_id": "a"}],
    }
    old = {"thread": 1, "answer": "a", "token": "old-render"}
    assert not apply_follow_event(thread, old, enabled=True, panel_open=True, active_thread=1)
    assert thread["selected_reply_id"] == "b"
    current = {**old, "token": "new-render"}
    assert apply_follow_event(thread, current, enabled=True, panel_open=True, active_thread=1)


def test_manual_evidence_does_not_reenable_explicitly_disabled_follow(screen):
    app, guided, _ = screen
    execute(app)
    execute(app)
    thread = app.session_state["threads"][0]
    identity = thread["messages"][1]["ui_reply_id"]
    app.toggle(key="follow_evidence").set_value(False).run()
    app.button(key="evidence_" + identity).click().run()
    assert not app.exception
    assert not app.toggle(key="follow_evidence").value
    assert thread["selected_reply_id"] == identity
    assert guided.call_count == 2


def test_rapid_follow_updates_share_token_until_manual_action():
    from app.ui.evidence_follow import follow_token

    thread = {"id": 1, "messages": []}
    original = follow_token(thread, 1)
    assert follow_token(thread, 1) == original
    assert follow_token(thread, 1) == original
    assert follow_token(thread, 2) != original
