"""Cross-feature checks: reviewed voice, account drafts and proactive ownership."""

from types import SimpleNamespace

from app.agent.llm_client import MockLLMClient
from app.agent.speech import SpeechService
from app.ui.composer_draft import apply_draft_event
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
    }
    _run(app)


def test_account_voice_is_only_submitted_after_review(monkeypatch):
    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    backend.add_thread()
    app = _app(backend, browser)
    thread = backend.list_threads("alice")[0].thread_id
    assert _workspace(app).thread["id"] == thread
    assert not app.chat_input[0].proto.disabled and app.chat_input[0].proto.accept_audio
    before = len(backend.sends)
    _record(app, monkeypatch)
    assert len(backend.sends) == before
    assert app.chat_input[0].proto.value == "Check ORD-057" and not app.text_area
    _send(app, "Check ORD-058")
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
    thread = _workspace(app).thread
    assert apply_draft_event(
        thread,
        {
            "thread": first,
            "token": thread["composer_token"],
            "sequence": 1,
            "text": "Edited private draft",
        },
    )
    _open(app, second)
    assert not app.text_area
    _open(app, first)
    assert app.chat_input[0].proto.value == "Edited private draft" and not app.text_area
    previous_key = app.chat_input[0].key
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
    assert any(b.label == "＋  New conversation" for b in app.button)
    for _ in range(3):
        _click(app, "New Chat")
        assert _workspace(app).thread["id"] == identifier
        assert len(backend.list_threads("alice")) == 1
    _click(app, "Morning Briefing")
    _click(app, "New Chat")
    assert _workspace(app).thread["id"] == identifier
    assert len(backend.list_threads("alice")) == 1
    assert app.session_state["ui_page"] == "copilot"


def test_private_answer_uses_approved_evidence_style_hook_and_native_question_text():
    from html import escape

    backend, browser = FakeWorkspaceBackend(), FakeBrowser("alice")
    thread = backend.add_thread()
    app = _open(_app(backend, browser), thread)
    text = "Check ORD-057\nKeep <b>literal</b> text"
    _send(app, text)
    user = app.chat_message[0]
    assert not user.get("html")
    assert any(escape(text) in item.value for item in user.markdown)
    reply_id = _workspace(app).thread["messages"][-1]["ui_reply_id"]
    evidence = next(b for b in app.button if b.label == "View Evidence")
    assert evidence.key.startswith("evidence_")
    evidence.click()
    _run(app)
    assert _workspace(app).thread["selected_reply_id"] == reply_id
    assert backend.snapshots[thread].messages[0].content == text


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
