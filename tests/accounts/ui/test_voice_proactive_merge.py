"""Cross-feature checks: reviewed voice, account drafts and proactive ownership."""

from types import SimpleNamespace

from app.agent.llm_client import MockLLMClient
from app.agent.speech import SpeechService
from tests.accounts.ui.test_account_chat_integration import (
    FakeBrowser,
    FakeWorkspaceBackend,
    _app,
    _click,
    _open,
    _run,
    _send,
    _workspace,
)
from tests.test_speech_service import wav


def _record(app, monkeypatch):
    transcriber = SimpleNamespace(transcribe=lambda audio: "Check ORD-057")
    monkeypatch.setattr(SpeechService, "from_env", lambda: SpeechService(transcriber))
    app.session_state["pending_speech"] = {
        "id": "account-recording",
        "thread_id": _workspace(app).thread["id"],
        "audio": wav(),
        "typed_text": "",
        "replace_draft_id": None,
    }
    _run(app)


def test_account_voice_is_only_submitted_after_review(monkeypatch):
    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    backend.add_thread()
    app = _app(backend, browser)
    thread = backend.list_threads("alice")[0].thread_id
    _open(app, thread)
    before = len(backend.sends)
    _record(app, monkeypatch)
    assert len(backend.sends) == before
    draft_key = next(w.key for w in app.text_area if "speech_draft" in w.key)
    app.text_area(key=draft_key).set_value("Check ORD-058")
    _click(app, "Send reviewed text")
    assert len(backend.sends) == before + 1
    assert _workspace(app).thread.get("speech_draft") is None
    assert any(m["content"] == "Check ORD-058" for m in _workspace(app).thread["messages"])
    _run(app)
    assert len(backend.sends) == before + 1


def test_voice_drafts_survive_thread_switch_but_never_account_switch(monkeypatch):
    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    backend.add_thread()
    app = _app(backend, browser)
    first = backend.list_threads("alice")[0].thread_id
    second = backend.create_thread("alice", "Second").thread_id
    _run(app)
    _open(app, first)
    _record(app, monkeypatch)
    _open(app, second)
    assert not app.text_area
    _open(app, first)
    assert any(w.value == "Check ORD-057" for w in app.text_area)
    previous_key = app.text_area[0].key
    browser.handle = "bob"
    _run(app)
    assert not app.text_area
    assert not _workspace(app).thread_drafts
    assert previous_key not in app.session_state


def test_signed_in_proactive_pages_use_verified_private_scope(monkeypatch, tmp_path):
    from app.ui import streamlit_app

    monkeypatch.setenv("PROACTIVE_DB_PATH", str(tmp_path / "private-proactive.sqlite3"))
    monkeypatch.setattr(
        streamlit_app,
        "agent_service",
        lambda: SimpleNamespace(parser=SimpleNamespace(client=MockLLMClient())),
    )
    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    app = _app(backend, browser)
    _click(app, "Watches & Alerts")
    scoped = _workspace(app).proactive_application
    alice = backend.current_user("alice")
    assert scoped.actor_provider() == alice.user_id
    assert scoped.workspace_provider() == alice.workspace_id
    app.selectbox(key="pa_field_watch_condition").set_value("status_equals")
    app.button(key="pa_submit_watch_create").click()
    _run(app)
    assert len(scoped.list_pending_actions()["data"]["items"]) == 1
    browser.handle = "bob"
    _run(app)
    _click(app, "Watches & Alerts")
    bob_scope = _workspace(app).proactive_application
    assert bob_scope.workspace_provider() == backend.current_user("bob").workspace_id
    assert bob_scope.list_pending_actions()["data"]["items"] == []
    assert not app.get("dialog")


def test_new_chat_returns_to_untouched_private_chat_without_creating_another():
    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    app = _app(backend, browser)
    _click(app, "New Chat")
    identifier = _workspace(app).thread["id"]
    _click(app, "Morning Briefing")
    _click(app, "New Chat")
    assert _workspace(app).thread["id"] == identifier
    assert len(backend.list_threads("alice")) == 1
    assert app.session_state["ui_page"] == "copilot"


def test_saved_proactive_reply_opens_review_and_returns_to_uuid_chat(monkeypatch, tmp_path):
    from app.ui import streamlit_app
    from app.ui.proactive.state import command

    monkeypatch.setenv("PROACTIVE_DB_PATH", str(tmp_path / "chat-proactive.sqlite3"))
    monkeypatch.setattr(
        streamlit_app,
        "agent_service",
        lambda: SimpleNamespace(parser=SimpleNamespace(client=MockLLMClient())),
    )
    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    thread = backend.add_thread()
    app = _app(backend, browser)
    _open(app, thread)
    _click(app, "Watches & Alerts")
    application = _workspace(app).proactive_application
    proposal = application.propose_action(
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
    assert proposal["ok"]
    _click(app, "AI Co-Pilot")

    def save_proactive(*args):
        saved = backend.save(*args)
        saved.reply.metadata["proactive"] = proposal
        return saved

    backend.on_send = save_proactive
    _send(app, "Watch this order")
    assert app.get("dialog")
    _click(app, "Review in Actions →")
    assert app.session_state["ui_page"] == "actions"
    _click(app, "Back to conversation")
    assert app.session_state["ui_page"] == "copilot"
    assert _workspace(app).thread["id"] == thread
    assert len(backend.sends) == 1
