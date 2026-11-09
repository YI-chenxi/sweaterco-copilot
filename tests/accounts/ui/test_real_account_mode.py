"""Exercise the actual P4 facade and P5 UI with offline storage and transport."""

from datetime import datetime, timedelta, timezone
from functools import partial
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from app.accounts.conversation import AccountBackend, ConversationError, runtime
from app.accounts.identity import postgres as identity_postgres
from app.accounts.identity.repository import InMemoryUserRepository
from app.accounts.identity.throttle import InMemoryAttemptRepository
from app.accounts.session import postgres as session_postgres
from app.accounts.session import service as session_service
from app.accounts.session.authorization import InMemoryAuthorizationRepository
from app.accounts.session.repository import InMemorySessionRepository
from app.accounts.storage import authorization as storage_authorization
from app.accounts.storage import database
from app.accounts.storage.models import ChatThread
from app.ui.accounts.real_backend import initialize_real_account_mode
from tests.accounts.conversation.fakes import FakeAgent, FakeRepository
from tests.accounts.ui.test_account_chat_integration import (
    FakeBrowser,
    _click,
    _html,
    _open,
    _run,
    _send,
    _workspace,
)
from tests.accounts.ui.test_persistent_chat import _TEXT

_FAKE_DATABASE = "postgresql://offline.invalid/account_test"
_FAKE_SECRET = "offline-test-key-" * 4
_PASSWORD = "A valid offline account test passphrase!"
_NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


class PrivateWorkspaceUsers(InMemoryUserRepository):
    def __init__(self, authorization):
        super().__init__()
        self.authorization = authorization

    def create(self, user):
        super().create(user)
        # Emulate the private workspace that P3's PostgreSQL trigger would create.
        self.authorization.grant_workspace(user.user_id, user.user_id)


class ThreadRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        self.titles = {}

    def lifecycle(self, principal, operation, *args):
        with self.lock:
            if operation == "list_threads":
                return [
                    ChatThread(
                        thread_id,
                        snapshot.owner_user_id,
                        snapshot.workspace_id,
                        self.titles[thread_id],
                        _NOW,
                        _NOW,
                    )
                    for thread_id, snapshot in self.threads.items()
                    if (snapshot.owner_user_id, snapshot.workspace_id)
                    == (principal.user_id, principal.workspace_id)
                ]
            assert operation == "create_thread"
            thread_id = self.create(principal)
            self.titles[thread_id] = args[0]
            return ChatThread(
                thread_id, principal.user_id, principal.workspace_id, args[0], _NOW, _NOW
            )


@pytest.fixture
def dependencies(monkeypatch):
    clock = [_NOW]
    authz = InMemoryAuthorizationRepository()
    users = PrivateWorkspaceUsers(authz)
    sessions = InMemorySessionRepository()
    attempts = InMemoryAttemptRepository()
    threads = ThreadRepository()
    connection = Mock(side_effect=AssertionError("Offline tests must not connect"))
    factory = Mock(return_value=connection)
    monkeypatch.setattr(database, "create_connection_factory", factory)
    monkeypatch.setattr(identity_postgres, "PostgresUserRepository", lambda _: users)
    monkeypatch.setattr(identity_postgres, "PostgresAttemptRepository", lambda _: attempts)
    monkeypatch.setattr(session_postgres, "PostgresSessionRepository", lambda _: sessions)
    monkeypatch.setattr(storage_authorization, "PostgresAuthorizationRepository", lambda _: authz)
    monkeypatch.setattr(runtime, "PostgresConversationRepository", lambda _: threads)
    monkeypatch.setattr(
        session_service,
        "SessionService",
        partial(session_service.SessionService, clock=lambda: clock[0]),
    )
    yield clock, factory, threads
    connection.assert_not_called()


def _page(mode):
    from app.ui.streamlit_app import main

    main(real_account_mode=mode)


def _app(mode, language="en"):
    app = AppTest.from_function(_page, args=(mode,), default_timeout=20)
    app.session_state["ui_language"] = language
    return _run(app)


def _init(agent, session=None):
    return initialize_real_account_mode(
        agent=agent,
        secrets={"DATABASE_URL": _FAKE_DATABASE, "ACCOUNT_SECRET": _FAKE_SECRET},
        session=session,
    )


@pytest.fixture
def account(dependencies):
    _, _, threads = dependencies
    agent = FakeAgent()
    browser = FakeBrowser()
    mode = _init(agent, browser)
    return mode, browser, agent, threads


def _form(app, action, username, password=_PASSWORD):
    app.text_input(key=f"account_{action}_username").set_value(username)
    app.text_input(key=f"account_{action}_password").set_value(password)
    return _click(app, "Sign in" if action == "login" else "Register")


def _register_and_login(mode, browser, username="alice"):
    mode.backend.register(username, _PASSWORD)
    login = mode.backend.login(username, _PASSWORD)
    browser.set_cookie(login.cookie)
    return login


def test_initialization_uses_p4_factory_with_injected_configuration(dependencies):
    _, factory, _ = dependencies
    browser = FakeBrowser()
    mode = _init(FakeAgent(), browser)

    assert type(mode.backend) is AccountBackend
    assert mode.session is browser
    assert _FAKE_SECRET not in repr(mode)
    assert _FAKE_DATABASE not in repr(mode)
    factory.assert_called_once_with(_FAKE_DATABASE)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"DATABASE_URL": _FAKE_DATABASE},
        {"ACCOUNT_SECRET": _FAKE_SECRET},
        {"DATABASE_URL": "invalid-offline-url", "ACCOUNT_SECRET": _FAKE_SECRET},
        {"DATABASE_URL": _FAKE_DATABASE, "ACCOUNT_SECRET": "too-short"},
    ],
)
def test_invalid_configuration_fails_closed_without_showing_diagnostics(config, dependencies):
    _, factory, _ = dependencies
    mode = initialize_real_account_mode(agent=FakeAgent(), secrets=config, session=FakeBrowser())
    app = _app(mode)

    assert mode.backend is None and mode.session is None
    factory.assert_not_called()
    assert app.error[0].value == "Account service is not configured. Contact the app maintainer."
    assert not app.tabs and not app.chat_input and not app.chat_message
    assert "threads" not in app.session_state
    assert "account_workspace" not in app.session_state


def test_backend_without_a_session_transport_does_not_offer_account_actions(dependencies):
    mode = _init(FakeAgent())
    app = _app(mode, language="zh")

    assert type(mode.backend) is AccountBackend
    assert app.info[0].value == "尚未配置安全的浏览器登录方式。"
    assert not app.tabs and not app.chat_input and not app.chat_message


def test_configuration_factory_failure_shows_safe_message_and_reinitializes(dependencies):
    _, factory, _ = dependencies
    connection = factory.return_value
    factory.side_effect = RuntimeError("private test configuration detail")
    failed = _init(FakeAgent(), FakeBrowser())
    app = _app(failed)
    assert failed.backend is None
    assert "private test configuration detail" not in app.error[0].value

    factory.side_effect = None
    factory.return_value = connection
    ready = _init(FakeAgent(), FakeBrowser())
    app = _app(ready)
    assert type(ready.backend) is AccountBackend
    assert [tab.label for tab in app.tabs] == ["Sign in", "Register"]
    assert not app.error


def test_real_p4_register_login_list_open_send_and_logout_from_the_main_ui(account):
    mode, browser, agent, _ = account
    app = _app(mode)
    assert [tab.label for tab in app.tabs] == ["Sign in", "Register"]
    _form(app, "register", "alice")
    assert app.success and browser.read() is None
    _form(app, "login", "alice", "wrong offline password")
    assert app.error[0].value == "The username or password is incorrect."
    assert not app.chat_input and browser.read() is None

    _form(app, "login", "alice")
    handle = browser.read()
    principal = mode.backend.current_user(handle)
    assert handle not in repr(app.session_state)
    assert principal.user_id == principal.workspace_id
    _click(app, "New Chat")
    thread_id = _workspace(app).thread["id"]
    assert [item.thread_id for item in mode.backend.list_threads(handle)] == [thread_id]
    _send(app, _TEXT)

    saved = mode.backend.open_thread(handle, thread_id)
    assert saved.revision == 1
    assert saved.messages[0].content == _TEXT
    assert saved.messages[1].reply.evidence[0].reference == "ORD-057"
    assert saved.session.next_task_number == 2
    assert _TEXT in _html(app)
    assert any("orders.csv" in item.value for item in app.caption)
    assert not app.get("download_button")
    assert any("CSV export is unavailable" in item.value for item in app.caption)
    _run(app)
    assert agent.calls == 1

    _click(app, "Sign out")
    assert browser.read() is None and not app.chat_message
    assert _workspace(app).chat.selected is None
    with pytest.raises(ConversationError) as error:
        mode.backend.current_user(handle)
    assert error.value.code == "UNAUTHENTICATED"


@pytest.mark.parametrize("transition", ["invalid", "expired", "switch", "read_failure"])
def test_session_transition_clears_private_workspace(
    account, dependencies, monkeypatch, transition
):
    mode, browser, agent, _ = account
    clock, _, _ = dependencies
    alice = _register_and_login(mode, browser)
    thread = mode.backend.create_thread(alice.cookie.value, "Alice private thread")
    mode.backend.register("bob", _PASSWORD)
    bob = mode.backend.login("bob", _PASSWORD)
    mode.backend.create_thread(bob.cookie.value, "Bob private thread")

    app = _open(_app(mode), thread.thread_id)
    _send(app, _TEXT)
    workspace = _workspace(app)
    old_key = app.chat_input[0].key
    draft_key = workspace.widget("draft")
    app.session_state[draft_key] = "Alice-only unsent draft"
    if transition == "expired":
        clock[0] += timedelta(days=15)
    elif transition == "invalid":
        browser.handle = "invalid-offline-session"
    elif transition == "read_failure":
        monkeypatch.setattr(
            browser, "read", Mock(side_effect=RuntimeError("private test transport error"))
        )
    else:
        app.chat_input[0].set_value("stale Alice submission")
        browser.set_cookie(bob.cookie)

    _run(app)
    assert "Alice private thread" not in [item.value for item in app.text]
    assert _TEXT not in _html(app)
    assert not app.chat_message and not app.get("download_button")
    assert workspace.chat.selected is None and workspace.chat.submission is None
    assert old_key not in app.session_state and draft_key not in app.session_state
    assert agent.calls == 1
    if transition == "switch":
        assert workspace.owner.user_id == bob.user_id
        assert "Bob private thread" in [item.value for item in app.text]
        with pytest.raises(ConversationError) as error:
            mode.backend.open_thread(bob.cookie.value, thread.thread_id)
        assert error.value.code in {"FORBIDDEN", "NOT_FOUND"}
    else:
        assert workspace.owner is None and not app.chat_input
        if transition == "read_failure":
            assert app.error[0].value == "Could not update the browser session. Please try again."
        else:
            assert app.warning[0].value == "Your session ended. Sign in again."


@pytest.mark.parametrize(("failure", "revision"), [("before_claim", 0), ("after_commit", 1)])
def test_refresh_and_new_ui_session_only_read_unknown_outcome(account, failure, revision):
    mode, browser, agent, repository = account
    login = _register_and_login(mode, browser)
    thread_id = mode.backend.create_thread(login.cookie.value).thread_id
    app = _open(_app(mode), thread_id)
    repository.fail = failure
    _send(app, _TEXT)
    assert app.chat_input[0].proto.disabled
    assert any("save outcome is unknown" in item.value for item in app.warning)

    _click(app, "Refresh chat")
    assert (
        _workspace(app).thread["messages"] != [] if revision else app.chat_input[0].proto.disabled
    )
    fresh = _open(_app(mode), thread_id)
    assert len(fresh.chat_message) == revision * 2
    assert agent.calls == revision
    assert mode.backend.open_thread(login.cookie.value, thread_id).revision == revision


def test_evidence_export_stays_disabled_in_real_mode_session_only_preview(account):
    mode, browser, agent, _ = account
    _register_and_login(mode, browser)
    app = _app(mode)
    app.button(key="center_Order_Status").click()
    _run(app)
    _click(app, "Run analysis →")

    assert any("Not saved to chat history" in item.label for item in app.expander)
    assert any("CSV export is unavailable" in item.value for item in app.caption)
    assert not app.get("download_button") and agent.calls == 0


def _replaceable_page(modes):
    from app.ui.streamlit_app import main

    main(real_account_mode=modes[0])


def test_switch_to_failed_real_configuration_clears_previously_open_chat(account):
    mode, browser, _, _ = account
    login = _register_and_login(mode, browser)
    thread = mode.backend.create_thread(login.cookie.value, "Private saved chat")
    modes = [mode]
    app = AppTest.from_function(_replaceable_page, args=(modes,), default_timeout=20)
    _open(_run(app), thread.thread_id)
    _send(app, _TEXT)
    workspace = _workspace(app)
    old_key = app.chat_input[0].key
    modes[0] = initialize_real_account_mode(agent=FakeAgent(), secrets={})

    _run(app)
    assert app.error[0].value == "Account service is not configured. Contact the app maintainer."
    assert not app.chat_input and not app.chat_message
    assert _TEXT not in _html(app)
    assert old_key not in app.session_state
    assert "account_workspace" not in app.session_state
    assert workspace.chat.selected is None


def _message_of_length(length):
    return "  " + "文" * (length - 4) + "x\n"


@pytest.mark.parametrize("length", [31_999, 32_000])
def test_p4_message_limit_accepts_exact_original_text(account, length):
    mode, browser, agent, _ = account
    login = _register_and_login(mode, browser)
    thread_id = mode.backend.create_thread(login.cookie.value).thread_id
    app = _open(_app(mode), thread_id)
    text = _message_of_length(length)
    assert len(text) == length

    _send(app, text)
    saved = mode.backend.open_thread(login.cookie.value, thread_id)
    assert saved.messages[0].content == text and text in _html(app)
    assert agent.calls == 1


@pytest.mark.parametrize(
    ("language", "message"),
    [
        ("en", "Use 32,000 characters or fewer. Your text is still in the input box."),
        ("zh", "消息不能超过 32,000 个字符。原文已保留在输入框中。"),
    ],
)
def test_over_limit_restores_unmodified_composer_and_does_not_call_p4(
    account, monkeypatch, language, message
):
    mode, browser, agent, _ = account
    login = _register_and_login(mode, browser)
    thread_id = mode.backend.create_thread(login.cookie.value).thread_id
    app = _open(_app(mode, language=language), thread_id)
    original_key = app.chat_input[0].key
    handle_turn = Mock(wraps=mode.backend.handle_turn)
    monkeypatch.setattr(mode.backend, "handle_turn", handle_turn)
    text = _message_of_length(32_001)

    _send(app, text)
    assert app.chat_input[0].key == original_key
    assert app.chat_input[0].proto.set_value
    assert app.chat_input[0].proto.value == text
    assert any(item.value == message for item in app.warning)
    assert not app.chat_message and _workspace(app).chat.submission is None
    handle_turn.assert_not_called()
    assert agent.calls == 0

    _run(app)
    handle_turn.assert_not_called()
    corrected = text[:-2] + text[-1]
    _send(app, corrected)
    assert len(corrected) == 32_000
    assert handle_turn.call_count == 1 and agent.calls == 1
    assert mode.backend.open_thread(login.cookie.value, thread_id).messages[0].content == corrected
