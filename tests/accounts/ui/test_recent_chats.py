"""Exercise the reusable history panel through native Streamlit interactions."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from app.accounts.conversation import AccountBackend, ConversationError, Principal
from app.accounts.conversation.contracts import DisplayMessage, OpenedConversation
from app.accounts.storage.models import ChatThread
from app.agent.contracts import AgentReply, Evidence
from app.agent.turn_service import ChatSession
from tests.accounts.ui.test_account_ui import _DELETE_COOKIE, FakeBrowserSession

_ALICE = Principal("alice", "workspace-a", "session-a")
_BOB = Principal("bob", "workspace-b", "session-b")
_HANDLE = "test-only-session-a"
_OTHER_HANDLE = "test-only-session-b"
_FIRST = "a9b50a26-a930-402d-9957-adfd7cb9dcd2"
_SECOND = "aac52147-09a7-46e9-a4be-971935bc8ea0"
_STATE = "account_recent_chats"
_NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def _thread(thread_id, title, owner=_ALICE):
    return ChatThread(thread_id, owner.user_id, owner.workspace_id, title, _NOW, _NOW)


def _page(backend, session, with_account):
    import streamlit as st

    from app.ui.accounts.panels import render_account_panel
    from app.ui.accounts.recent_chats import render_recent_chats
    from app.ui.themes import native_styles

    st.html("<style>" + native_styles(st.session_state.get("ui_theme", "green")) + "</style>")
    if with_account and render_account_panel(backend, session) is None:
        st.session_state["test_opened"] = None
    else:
        st.session_state["test_opened"] = render_recent_chats(backend, session)


@pytest.fixture
def backend():
    fake = Mock(spec=AccountBackend)
    fake.current_user.side_effect = {_HANDLE: _ALICE, _OTHER_HANDLE: _BOB}.__getitem__
    fake.list_threads.return_value = [_thread(_FIRST, "Alice's chat")]
    reply = AgentReply("原始回复", [Evidence("orders.csv", "ORD-057", "20 pieces")])
    messages = (
        DisplayMessage("message-1", "request", 1, "user", "  不要发 ORD-057 20 件\n", None),
        DisplayMessage("message-2", "request", 2, "assistant", reply.text, reply),
    )
    fake.open_thread.return_value = OpenedConversation(_FIRST, 1, messages, ChatSession())
    fake.logout.return_value = _DELETE_COOKIE
    return fake


def _run(app):
    app.run()
    assert not app.exception
    return app


def _app(backend, session=None, *, language="en", theme="green", with_account=False):
    if session is None:
        session = FakeBrowserSession(_HANDLE)
    app = AppTest.from_function(_page, args=(backend, session, with_account), default_timeout=20)
    app.session_state["ui_language"] = language
    app.session_state["ui_theme"] = theme
    return _run(app)


def _click(app, label):
    next(button for button in app.button if button.label == label).click()
    _run(app)


def _text(app):
    return [element.value for element in app.text]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("theme", ["day", "green", "night"])
def test_list_chats_in_supported_languages_and_themes(backend, language, theme):
    app = _app(backend, language=language, theme=theme)

    backend.list_threads.assert_called_once_with(_HANDLE)
    assert _text(app) == ["Alice's chat"]
    assert app.subheader[0].value == ("Recent Chats" if language == "en" else "最近聊天")
    assert [button.label for button in app.button] == (
        ["New Chat", "Open Chat"] if language == "en" else ["新建聊天", "打开聊天"]
    )
    assert _HANDLE not in repr(app.session_state)


def test_open_keeps_original_text_evidence_and_missing_reply(backend):
    backend.list_threads.return_value = [
        _thread(_SECOND, "Same title"),
        _thread(_FIRST, "Same title"),
    ]
    app = _app(backend)
    next(button for button in app.button if button.key.endswith(f"{_FIRST}_open")).click()
    _run(app)

    backend.open_thread.assert_called_once_with(_HANDLE, _FIRST)
    snapshot = app.session_state["test_opened"]
    assert snapshot is backend.open_thread.return_value
    assert snapshot.messages[0].content == "  不要发 ORD-057 20 件\n"
    assert snapshot.messages[0].reply is None
    assert snapshot.messages[1].reply.evidence == [Evidence("orders.csv", "ORD-057", "20 pieces")]
    backend.handle_turn.assert_not_called()


def test_new_chat_refreshes_list_and_opens_created_uuid(backend):
    created = _thread(_SECOND, "新对话")
    backend.create_thread.return_value = created
    backend.list_threads.side_effect = [[], [], [created]]
    backend.open_thread.return_value = OpenedConversation(_SECOND, 0, (), ChatSession())
    app = _app(backend, language="zh")
    assert app.info[0].value == "暂无聊天记录。"

    _click(app, "新建聊天")

    backend.create_thread.assert_called_once_with(_HANDLE, "新对话")
    backend.open_thread.assert_called_once_with(_HANDLE, _SECOND)
    assert "新对话" in _text(app)
    assert app.session_state["test_opened"].messages == ()


def test_rename_validates_title_and_refreshes_without_reopening(backend):
    app = _app(backend)
    _click(app, "Open Chat")
    _click(app, "Rename Chat")
    app.text_input[0].set_value("   ")
    _click(app, "Save title")
    assert app.error[0].value == "Enter a chat title of 1 to 200 characters."
    backend.rename_thread.assert_not_called()

    def rename(handle, thread_id, title):
        updated = _thread(thread_id, title)
        backend.list_threads.return_value = [updated]
        return updated

    backend.rename_thread.side_effect = rename
    app.text_input[0].set_value("换个标题")
    _click(app, "Save title")

    backend.rename_thread.assert_called_once_with(_HANDLE, _FIRST, "换个标题")
    backend.open_thread.assert_called_once_with(_HANDLE, _FIRST)
    assert _text(app) == ["换个标题", "换个标题"]
    assert not app.text_input


def test_delete_requires_confirmation_and_cancel_never_calls_backend(backend):
    app = _app(backend)
    _click(app, "Open Chat")
    _click(app, "Delete Chat")
    assert app.warning[0].value == "Permanently delete this conversation?"
    backend.delete_thread.assert_not_called()
    _click(app, "Cancel")
    assert not app.warning
    assert app.session_state["test_opened"].thread_id == _FIRST
    backend.delete_thread.assert_not_called()
    _click(app, "Delete Chat")

    def delete(handle, thread_id):
        backend.list_threads.return_value = []

    backend.delete_thread.side_effect = delete
    _click(app, "Confirm deletion")

    backend.delete_thread.assert_called_once_with(_HANDLE, _FIRST)
    assert app.session_state["test_opened"] is None
    assert app.info[0].value == "No conversations yet."
    assert "Alice's chat" not in _text(app)


@pytest.mark.parametrize("change", ["switch", "logout", "new_session"])
def test_auth_transition_removes_opened_conversation_and_rename_draft(backend, change):
    session = FakeBrowserSession(_HANDLE)
    app = _app(backend, session, with_account=True)
    _click(app, "Open Chat")
    _click(app, "Rename Chat")
    title = next(element for element in app.text_input if element.label == "Chat title")
    old_key = title.key
    title.set_value("Alice's unsaved private title")
    backend.rename_thread.side_effect = ConversationError("STORAGE_ERROR")
    _click(app, "Save title")
    assert app.session_state[old_key] == "Alice's unsaved private title"

    if change == "logout":
        _click(app, "Sign out")
        assert _STATE not in app.session_state
        backend.logout.assert_called_once_with(_HANDLE)
    else:
        owner = _BOB if change == "switch" else replace(_ALICE, session_id="new-session")
        backend.current_user.side_effect = {_OTHER_HANDLE: owner}.__getitem__
        session.handle = _OTHER_HANDLE
        backend.list_threads.return_value = [_thread(_SECOND, "Current chat", owner)]
        _run(app)
        backend.list_threads.assert_called_with(_OTHER_HANDLE)
        assert _text(app) == ["Signed in", "Current chat"]

    assert "Alice's chat" not in _text(app)
    assert old_key not in app.session_state
    assert app.session_state["test_opened"] is None


def test_confirmation_click_from_old_account_cannot_delete_in_new_account(backend):
    session = FakeBrowserSession(_HANDLE)
    app = _app(backend, session)
    _click(app, "Open Chat")
    _click(app, "Delete Chat")
    next(button for button in app.button if button.label == "Confirm deletion").click()

    session.handle = _OTHER_HANDLE
    backend.list_threads.return_value = [_thread(_SECOND, "Bob's chat", _BOB)]
    _run(app)

    backend.delete_thread.assert_not_called()
    assert not app.warning
    assert _text(app) == ["Bob's chat"]
    assert app.session_state["test_opened"] is None


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("FORBIDDEN", "You do not have access to this conversation or workspace."),
        ("UNAUTHENTICATED", "Your session ended. Sign in again."),
        ("IDENTITY_CHANGED", "Your session ended. Sign in again."),
    ],
)
@pytest.mark.parametrize("operation", ["current_user", "list_threads", "open_thread"])
def test_access_errors_clear_history_and_never_show_previous_chats(
    backend, code, message, operation
):
    app = _app(backend)
    _click(app, "Open Chat")
    getattr(backend, operation).side_effect = ConversationError(code)
    if operation == "open_thread":
        _click(app, "Open Chat")
    else:
        _run(app)
    assert (app.error if code == "FORBIDDEN" else app.warning)[0].value == message
    assert _STATE not in app.session_state
    assert "Alice's chat" not in _text(app)
    assert app.session_state["test_opened"] is None


def test_foreign_list_entries_are_rejected_without_display(backend):
    backend.list_threads.return_value = [
        _thread(_FIRST, "Alice's chat"),
        _thread(_SECOND, "Bob's private chat", _BOB),
    ]
    app = _app(backend)
    assert app.error[0].value == "You do not have access to this conversation or workspace."
    assert not app.text
    assert not app.button
    backend.open_thread.assert_not_called()


def test_storage_failure_shows_safe_text_then_list_recovers(backend):
    app = _app(backend)
    _click(app, "Open Chat")
    backend.list_threads.side_effect = RuntimeError("internal test detail")
    _run(app)

    assert app.error[0].value == "Could not load chats. Please try again."
    assert _STATE not in app.session_state
    assert app.session_state["test_opened"] is None
    backend.list_threads.side_effect = None
    _run(app)
    assert _text(app) == ["Alice's chat"]
    assert not app.error
    assert app.session_state["test_opened"] is None


def test_no_handle_hides_history_and_never_calls_backend(backend):
    app = _app(backend, FakeBrowserSession())
    assert app.info[0].value == "Sign in to view your chats."
    assert not backend.mock_calls
    assert _STATE not in app.session_state


def test_removed_thread_invalidates_open_snapshot(backend):
    app = _app(backend)
    _click(app, "Open Chat")
    backend.list_threads.return_value = []
    _run(app)

    assert app.info[0].value == "No conversations yet."
    assert app.session_state["test_opened"] is None


def test_uncertain_deletion_warns_without_retry(backend):
    backend.delete_thread.side_effect = ConversationError("STORAGE_OUTCOME_UNKNOWN")
    app = _app(backend)
    _click(app, "Open Chat")
    _click(app, "Delete Chat")
    _click(app, "Confirm deletion")

    assert app.error[0].value == (
        "The change may have been saved. Check your chat list before trying again."
    )
    _run(app)
    backend.delete_thread.assert_called_once_with(_HANDLE, _FIRST)
    assert "Confirm deletion" not in [button.label for button in app.button]


def test_expired_deletion_clears_confirmation_and_conversation(backend):
    backend.delete_thread.side_effect = ConversationError("UNAUTHENTICATED")
    app = _app(backend)
    _click(app, "Open Chat")
    _click(app, "Delete Chat")
    _click(app, "Confirm deletion")

    assert app.warning[0].value == "Your session ended. Sign in again."
    assert not app.button
    assert not app.text
    assert _STATE not in app.session_state
    assert app.session_state["test_opened"] is None
