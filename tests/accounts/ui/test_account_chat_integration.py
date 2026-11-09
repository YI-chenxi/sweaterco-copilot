"""Run the actual Co-Pilot layout with an explicitly offline account backend."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from app.accounts.conversation import ConversationError, LoginResult, PendingTurn
from app.accounts.session.models import SessionCookie
from tests.accounts.ui.test_persistent_chat import (
    _BOB,
    _TEXT,
    FakeAccountBackend,
    _id,
    _pair,
)


class FakeBrowser:
    def __init__(self, handle=None):
        self.handle = handle

    def read(self):
        return self.handle

    def set_cookie(self, cookie):
        self.handle = cookie.value

    def clear_cookie(self, cookie):
        self.handle = None


class FakeWorkspaceBackend(FakeAccountBackend):
    def __init__(self):
        super().__init__()
        self.registrations = []
        self.logouts = []

    def register(self, username, password):
        self.registrations.append(username)

    def login(self, username, password):
        principal = self.current_user(username)
        return LoginResult(
            principal.user_id,
            username,
            principal.session_id,
            principal.workspace_id,
            SessionCookie("offline-session", username, max_age=60),
        )

    def logout(self, handle):
        self.logouts.append(handle)
        self.identities.pop(handle)
        return SessionCookie("offline-session", "", max_age=0)

    def create_thread(self, handle, title):
        thread_id = self.add_thread(self.current_user(handle), title)
        return next(item for item in self.list_threads(handle) if item.thread_id == thread_id)

    def delete_thread(self, handle, thread_id):
        self._check_thread(handle, thread_id)
        del self.owners[thread_id]
        del self.snapshots[thread_id]


def _page(backend, browser):
    from app.ui.accounts.workspace import AccountTestMode
    from app.ui.streamlit_app import main

    main(AccountTestMode(backend, browser))


def _run(app):
    app.run(timeout=20)
    assert not app.exception
    return app


def _app(backend, browser, *, language="en", theme="green"):
    app = AppTest.from_function(_page, args=(backend, browser), default_timeout=20)
    app.session_state["ui_language"] = language
    app.session_state["ui_theme"] = theme
    return _run(app)


def _click(app, label):
    next(button for button in app.button if button.label == label).click()
    return _run(app)


def _open(app, thread_id):
    next(button for button in app.button if button.key.endswith(f"{thread_id}_open")).click()
    return _run(app)


def _workspace(app):
    return app.session_state["account_workspace"]


def _html(app):
    return "\n".join(element.proto.body for element in app.get("html"))


def _send(app, text):
    app.chat_input[0].set_value(text)
    return _run(app)


@pytest.fixture
def account():
    backend = FakeWorkspaceBackend()
    thread_id = backend.add_thread(title="Alice private thread")
    return backend, FakeBrowser("alice"), thread_id


def test_unauthenticated_registration_and_login_gate_the_workspace(account):
    backend, browser, _ = account
    browser.handle = None
    app = _app(backend, browser)
    assert "Offline account test mode" in app.info[0].value
    assert [tab.label for tab in app.tabs] == ["Sign in", "Register"]
    assert not app.chat_input and not app.chat_message
    assert not backend.reads and not backend.sends

    app.text_input(key="account_register_username").set_value("new-user")
    app.text_input(key="account_register_password").set_value("offline-only")
    _click(app, "Register")
    assert backend.registrations == ["new-user"]
    assert app.success and not app.chat_input

    app.text_input(key="account_login_username").set_value("alice")
    app.text_input(key="account_login_password").set_value("offline-only")
    _click(app, "Sign in")
    assert not app.tabs
    assert app.chat_input[0].proto.disabled
    assert "Recent Chats" in [header.value for header in app.subheader]
    assert "Alice private thread" in [text.value for text in app.text]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("theme", ["day", "green", "night"])
def test_account_sidebar_and_composer_use_existing_language_and_theme(account, language, theme):
    backend, browser, _ = account
    app = _app(backend, browser, language=language, theme=theme)
    assert app.session_state["ui_theme"] == theme
    assert all(app.button(key=f"theme_{name}") for name in ("day", "green", "night"))
    assert app.selectbox(key="ui_language").value == language
    assert ("Recent Chats" if language == "en" else "最近聊天") in [
        header.value for header in app.subheader
    ]
    assert len(app.chat_input) == 1
    assert app.chat_input[0].proto.disabled


def test_open_history_preserves_original_text_and_selects_evidence_by_saved_id(account):
    backend, browser, thread_id = account
    first = _pair(_id())
    second = _pair(_id(), "Next question", sequence=3)
    first[1].reply.evidence[:] = [replace(first[1].reply.evidence[0], source="first.csv")]
    second[1].reply.evidence[:] = [replace(second[1].reply.evidence[0], source="second.csv")]
    backend.snapshots[thread_id] = replace(
        backend.snapshots[thread_id], messages=first + second, revision=2
    )
    app = _open(_app(backend, browser), thread_id)

    assert backend.reads == [("alice", thread_id)]
    assert _workspace(app).thread["id"] == thread_id
    assert _TEXT in _html(app)
    assert len(app.chat_message) == 4
    assert any("second.csv" in element.value for element in app.caption)
    next(button for button in app.button if button.key.endswith(f"evidence_{first[1].id}")).click()
    _run(app)
    assert any("first.csv" in element.value for element in app.caption)
    assert not any("second.csv" in element.value for element in app.caption)

    app.selectbox(key="ui_language").set_value("zh")
    _run(app)
    assert _TEXT in _html(app)
    assert not backend.sends


def test_keyboard_send_reloads_p4_history_and_reruns_do_not_duplicate(account):
    backend, browser, thread_id = account
    app = _open(_app(backend, browser), thread_id)
    old_key = app.chat_input[0].key
    _send(app, _TEXT)
    assert len(backend.sends) == 1
    assert backend.sends[0][:3] == ("alice", thread_id, _TEXT)
    assert backend.reads == [("alice", thread_id)] * 2
    assert _workspace(app).thread["messages"][0]["content"] == _TEXT
    assert len(app.chat_message) == 2 and _TEXT in _html(app)
    assert app.chat_input[0].key != old_key
    _run(app)
    assert len(backend.sends) == 1


def test_new_chat_and_confirmed_delete_update_active_uuid(account):
    backend, browser, _ = account
    app = _click(_app(backend, browser), "New Chat")
    thread_id = _workspace(app).thread["id"]
    assert thread_id in backend.owners and not app.chat_input[0].proto.disabled
    _click(app, "Delete Chat")
    assert thread_id in backend.owners
    _click(app, "Confirm deletion")
    assert thread_id not in backend.owners
    assert _workspace(app).thread["id"] is None and app.chat_input[0].proto.disabled


@pytest.mark.parametrize(
    ("status", "banner"),
    [("processing", "A turn is processing"), ("failed", "The previous turn needs verification")],
)
def test_pending_snapshot_blocks_send_and_refresh_is_read_only(account, status, banner):
    backend, browser, thread_id = account
    request_id = _id()
    backend.snapshots[thread_id] = replace(
        backend.snapshots[thread_id],
        messages=_pair(request_id)[:1],
        pending=PendingTurn(request_id, status),
    )
    app = _open(_app(backend, browser), thread_id)
    assert app.chat_input[0].proto.disabled and _TEXT in _html(app)
    assert any(banner in item.value for item in [*app.info, *app.warning])
    _click(app, "Refresh chat")
    assert backend.reads == [("alice", thread_id)] * 2
    assert not backend.sends


def test_unknown_outcome_refresh_restores_committed_history_without_resend(account):
    backend, browser, thread_id = account

    def save_then_lose_receipt(*args):
        backend.save(*args)
        raise ConversationError("STORAGE_OUTCOME_UNKNOWN")

    backend.on_send = save_then_lose_receipt
    app = _open(_app(backend, browser), thread_id)
    _send(app, _TEXT)
    assert app.chat_input[0].proto.disabled
    assert any("save outcome is unknown" in warning.value for warning in app.warning)
    assert not app.chat_message
    _click(app, "Refresh chat")
    assert len(backend.sends) == 1
    assert len(app.chat_message) == 2 and not app.chat_input[0].proto.disabled


def test_explicit_retry_keeps_original_request_id_and_text(account):
    backend, browser, thread_id = account

    def unavailable(*args):
        raise ConversationError("STORAGE_ERROR")

    backend.on_send = unavailable
    app = _open(_app(backend, browser), thread_id)
    _send(app, _TEXT)
    assert len(backend.sends) == 1
    backend.on_send = None
    _click(app, "Retry same message")
    assert backend.sends == [backend.sends[0]] * 2
    assert _TEXT in _html(app)


@pytest.mark.parametrize("transition", ["logout", "switch", "expired"])
def test_identity_transition_clears_history_evidence_and_account_drafts(account, transition):
    backend, browser, thread_id = account
    bob_thread = backend.add_thread(_BOB, "Bob private thread")
    backend.snapshots[thread_id] = replace(
        backend.snapshots[thread_id], messages=_pair(_id()), revision=1
    )
    app = _open(_app(backend, browser), thread_id)
    workspace = _workspace(app)
    composer_key = app.chat_input[0].key
    app.session_state["pending_speech"] = {"audio": b"private-test-recording"}
    app.session_state["text_translations"] = {"private": "private"}
    app.session_state[workspace.widget("unsent_draft")] = "private unsent text"
    workspace.set_preview(
        "Order Status", {"order_id": "ORD-057"}, workspace.thread["messages"][1]["reply"]
    )
    if transition == "logout":
        _click(app, "Sign out")
        assert backend.logouts == ["alice"]
    elif transition == "expired":
        del backend.identities["alice"]
        _run(app)
        assert any("session ended" in warning.value for warning in app.warning)
    else:
        app.chat_input[0].set_value("old account event")
        browser.handle = "bob"
        _run(app)
        assert "Bob private thread" in [text.value for text in app.text]
        _open(app, bob_thread)

    assert "Alice private thread" not in [text.value for text in app.text]
    assert _TEXT not in _html(app)
    assert not any("orders.csv" in element.value for element in app.caption)
    assert composer_key not in app.session_state
    assert "pending_speech" not in app.session_state
    assert "text_translations" not in app.session_state
    assert workspace.preview is None and workspace.chat.submission is None
    assert not backend.sends


def test_workflow_result_is_an_explicit_preview_and_never_enters_persisted_history(account):
    backend, browser, thread_id = account
    app = _open(_app(backend, browser), thread_id)
    app.button(key="center_Order_Status").click()
    _run(app)
    _click(app, "Run analysis →")

    assert any("Not saved to chat history" in element.label for element in app.expander)
    assert backend.snapshots[thread_id].messages == ()
    assert not backend.sends and not app.chat_message
    assert _workspace(app).preview[2].evidence
    assert any("orders.csv" in element.value for element in app.caption)


def test_sidebar_session_rejection_hides_the_existing_private_workspace(account):
    backend, browser, thread_id = account
    backend.snapshots[thread_id] = replace(
        backend.snapshots[thread_id], messages=_pair(_id()), revision=1
    )
    app = _open(_app(backend, browser), thread_id)

    def reject_listing(handle):
        raise ConversationError("IDENTITY_CHANGED")

    backend.list_threads = reject_listing
    _run(app)

    assert not app.chat_message and not app.chat_input
    assert _workspace(app).owner is None and _workspace(app).chat.selected is None
    assert any("session ended" in element.value for element in app.warning)
    assert not any("orders.csv" in element.value for element in app.caption)
    assert not backend.sends


def test_sign_out_drops_unresolved_send_and_never_retries_it(account):
    backend, browser, thread_id = account
    app = _open(_app(backend, browser), thread_id)

    def disconnected(*args):
        raise ConversationError("STORAGE_OUTCOME_UNKNOWN")

    backend.on_send = disconnected
    _send(app, _TEXT)
    assert _workspace(app).chat.submission.phase == "unknown"
    _click(app, "Sign out")
    assert _workspace(app).chat.submission is None and not app.chat_input
    assert len(backend.sends) == 1


@pytest.mark.parametrize(
    "change_at", ["before_list", "during_list", "before_open", "during_open", "after_sidebar"]
)
@pytest.mark.parametrize("next_handle", ["bob", None])
def test_identity_change_during_sidebar_hides_all_private_data(
    account, monkeypatch, change_at, next_handle
):
    backend, browser, thread_id = account
    bob_thread = backend.add_thread(_BOB, "Bob private thread")
    backend.snapshots[thread_id] = replace(
        backend.snapshots[thread_id], messages=_pair(_id()), revision=1
    )
    app = _open(_app(backend, browser), thread_id)
    workspace = _workspace(app)
    draft_key = workspace.widget("draft")
    app.session_state[draft_key] = "Alice's unsent draft"
    app.session_state["account_display_identity"] = (workspace.owner, "Alice private username")
    original_listing = backend.list_threads
    listing = Mock(wraps=original_listing)
    monkeypatch.setattr(backend, "list_threads", listing)
    reads_before = list(backend.reads)

    if change_at in {"before_open", "during_open"}:
        next(button for button in app.button if button.key.endswith(f"{thread_id}_open")).click()
    if change_at in {"before_list", "before_open", "after_sidebar"}:
        reads_as_alice = 1 if change_at == "before_list" else 3
        read = Mock(side_effect=["alice"] * reads_as_alice + [next_handle] * 4)
        monkeypatch.setattr(browser, "read", read)
    elif change_at == "during_list":

        def change_after_listing(handle):
            threads = original_listing(handle)
            if next_handle is None:
                del backend.identities[handle]
            else:
                browser.handle = next_handle
            return threads

        listing.side_effect = change_after_listing
    else:
        backend.on_read = lambda: setattr(browser, "handle", next_handle)

    _run(app)
    if change_at == "before_list":
        listing.assert_not_called()
    if change_at == "during_open":
        assert backend.reads == reads_before + [("alice", thread_id)]
    else:
        assert backend.reads == reads_before
    assert "Alice private thread" not in [item.value for item in app.text]
    assert "Bob private thread" not in [item.value for item in app.text]
    assert not any("Alice private username" in item.value for item in app.text)
    assert "account_display_identity" not in app.session_state
    assert _TEXT not in _html(app) and not app.chat_message and not app.chat_input
    assert not any("orders.csv" in item.value for item in app.caption)
    assert any("session ended" in item.value for item in app.warning)
    assert workspace.owner is None and workspace.chat.selected is None
    assert draft_key not in app.session_state
    assert "account_recent_chats" not in app.session_state

    monkeypatch.undo()
    backend.on_read = None
    browser.handle = "bob"
    _run(app)
    assert [item.value for item in app.text if "private thread" in item.value] == [
        "Bob private thread"
    ]
    _open(app, bob_thread)
    assert workspace.thread["id"] == bob_thread and not app.chat_message
    assert not backend.sends
