"""Render the P5 account panel with isolated browser-session and backend doubles."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from app.accounts.conversation import AccountBackend, ConversationError, LoginResult, Principal
from app.accounts.session.models import SessionCookie

_TEST_PASSWORD = "valid-pass12"
_COOKIE = SessionCookie("test-session", "opaque-test-only-token", max_age=300)
_DELETE_COOKIE = replace(_COOKIE, value="", max_age=0)
_PRINCIPAL = Principal("test-user", "test-workspace", "test-session-id")


class FakeBrowserSession:
    def __init__(self, handle=None):
        self.handle = handle

    def read(self):
        return self.handle

    def set_cookie(self, cookie):
        assert cookie == _COOKIE
        self.handle = cookie.value

    def clear_cookie(self, cookie):
        assert cookie == _DELETE_COOKIE
        self.handle = None


def _page(backend, session):
    import streamlit as st

    from app.ui.accounts.panels import render_account_panel
    from app.ui.themes import native_styles

    st.html("<style>" + native_styles(st.session_state.get("ui_theme", "green")) + "</style>")
    st.session_state["test_verified_principal"] = render_account_panel(backend, session)


@pytest.fixture
def backend():
    fake = Mock(spec=AccountBackend)
    fake.login.return_value = LoginResult(
        _PRINCIPAL.user_id, "alice", _PRINCIPAL.session_id, _PRINCIPAL.workspace_id, _COOKIE
    )
    fake.current_user.return_value = _PRINCIPAL
    fake.logout.return_value = _DELETE_COOKIE
    return fake


def _app(backend, session, *, language="en", theme="green"):
    app = AppTest.from_function(_page, args=(backend, session), default_timeout=20)
    app.session_state["ui_language"] = language
    app.session_state["ui_theme"] = theme
    return _run(app)


def _run(app):
    app.run()
    assert not app.exception
    return app


def _submit(app, form, *, username="alice", password=_TEST_PASSWORD):
    app.text_input(key=f"account_{form}_username").set_value(username)
    app.text_input(key=f"account_{form}_password").set_value(password)
    label = {
        ("en", "register"): "Register",
        ("zh", "register"): "注册",
        ("en", "login"): "Sign in",
        ("zh", "login"): "登录",
    }[app.session_state["ui_language"], form]
    next(button for button in app.button if button.label == label).click()
    return _run(app)


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("theme", ["day", "green", "night"])
def test_account_forms_render_in_supported_languages_and_themes(backend, language, theme):
    app = _app(backend, FakeBrowserSession(), language=language, theme=theme)
    assert [tab.label for tab in app.tabs] == (
        ["Sign in", "Register"] if language == "en" else ["登录", "注册"]
    )
    assert app.text_input(key="account_register_username").label == (
        "Username" if language == "en" else "用户名"
    )
    assert app.text_input(key="account_login_password").proto.type == 1  # masked
    assert app.text_input(key="account_register_password").proto.type == 1
    requirement = (
        "Use 6–15 characters; avoid your username and common passwords."
        if language == "en"
        else "密码需为 6–15 个字符，且不能与用户名相同或使用常见弱密码。"
    )
    assert requirement in [caption.value for caption in app.caption]
    assert not backend.mock_calls


def test_missing_secure_transport_blocks_account_actions(backend):
    app = _app(backend, None)
    assert app.info[0].value == "Secure browser sign-in is not configured."
    assert not app.text_input
    assert app.session_state["test_verified_principal"] is None
    assert not backend.mock_calls


def test_register_validates_and_saves_without_auto_login(backend):
    session = FakeBrowserSession()
    app = _app(backend, session, language="zh")
    _submit(app, "register", username=" ")
    assert app.error[0].value == "请输入用户名和密码。"
    backend.register.assert_not_called()
    _submit(app, "register", password="  " + _TEST_PASSWORD)
    backend.register.assert_called_once_with("alice", "  " + _TEST_PASSWORD)
    backend.login.assert_not_called()
    assert app.success[0].value == "注册成功，请登录以继续。"
    assert session.handle is None


def test_login_validates_identity_and_logout_revokes_before_clearing_cookie(backend):
    session = FakeBrowserSession()
    app = _app(backend, session)
    _submit(app, "login")
    backend.login.assert_called_once_with("alice", _TEST_PASSWORD)
    backend.current_user.assert_called_once_with(_COOKIE.value)
    assert app.session_state["test_verified_principal"] == _PRINCIPAL
    assert "Signed in as alice" in [item.value for item in app.text]
    assert _COOKIE.value not in repr(app.session_state)

    original_clear = session.clear_cookie

    def clear_after_revoke(cookie):
        backend.logout.assert_called_once_with(_COOKIE.value)
        original_clear(cookie)

    session.clear_cookie = clear_after_revoke
    app.button(key="account_logout").click()
    _run(app)
    assert session.handle is None
    assert app.session_state["test_verified_principal"] is None
    assert "account_display_identity" not in app.session_state
    assert app.text_input(key="account_login_username")


def test_expired_session_clears_display_identity_and_prompts_login(backend):
    app = _app(backend, FakeBrowserSession(), language="zh")
    _submit(app, "login")
    backend.current_user.side_effect = ConversationError("UNAUTHENTICATED")
    _run(app)
    assert app.warning[0].value == "会话已结束，请重新登录。"
    assert app.session_state["test_verified_principal"] is None
    assert "account_display_identity" not in app.session_state
    assert app.text_input(key="account_login_username")


def test_changed_verified_session_does_not_reuse_previous_username(backend):
    session = FakeBrowserSession()
    app = _app(backend, session)
    _submit(app, "login")
    new_principal = Principal("another-user", "another-workspace", "another-session")
    backend.current_user.return_value = new_principal
    session.handle = "different-opaque-test-handle"
    _run(app)
    assert app.session_state["test_verified_principal"] == new_principal
    assert [item.value for item in app.text] == ["Signed in"]
    assert "account_display_identity" not in app.session_state


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ConversationError("INVALID_CREDENTIALS"), "用户名或密码不正确。"),
        (ConversationError("RATE_LIMITED"), "尝试次数过多，请稍后重试。"),
        (
            RuntimeError("raw test diagnostic with sensitive values"),
            "账号服务暂不可用，请稍后重试。",
        ),
    ],
)
def test_login_errors_show_localized_safe_feedback(backend, error, message):
    backend.login.side_effect = error
    session = FakeBrowserSession()
    app = _app(backend, session, language="zh")
    _submit(app, "login")
    assert [item.value for item in app.error] == [message]
    assert session.handle is None
    assert app.session_state["test_verified_principal"] is None


def test_failed_cookie_write_does_not_report_login_success(backend):
    session = FakeBrowserSession()
    session.set_cookie = Mock(side_effect=RuntimeError("raw test cookie diagnostic"))
    app = _app(backend, session)
    _submit(app, "login")
    assert app.error[0].value == "Could not update the browser session. Please try again."
    assert "account_display_identity" not in app.session_state
    assert app.session_state["test_verified_principal"] is None


def test_logout_error_keeps_current_identity_until_revocation_succeeds(backend):
    session = FakeBrowserSession(_COOKIE.value)
    session.clear_cookie = Mock()
    backend.logout.side_effect = RuntimeError("raw test logout diagnostic")
    app = _app(backend, session)
    app.button(key="account_logout").click()
    _run(app)
    assert app.error[0].value == "The account service is unavailable. Please try again later."
    assert app.session_state["test_verified_principal"] == _PRINCIPAL
    session.clear_cookie.assert_not_called()


def test_registration_policy_errors_are_localized(backend):
    backend.register.side_effect = ConversationError("INVALID_PASSWORD")
    app = _app(backend, FakeBrowserSession(), language="zh")
    _submit(app, "register")
    assert app.error[0].value == "密码需为 6–15 个字符，且不能与用户名相同或使用常见弱密码。"
    assert not app.success
    backend.login.assert_not_called()


def test_failed_cookie_clear_after_revocation_removes_verified_identity(backend):
    session = FakeBrowserSession(_COOKIE.value)
    session.clear_cookie = Mock(side_effect=RuntimeError("raw test cookie diagnostic"))
    app = _app(backend, session)
    app.button(key="account_logout").click()
    _run(app)
    backend.logout.assert_called_once_with(_COOKIE.value)
    session.clear_cookie.assert_called_once_with(_DELETE_COOKIE)
    assert app.session_state["test_verified_principal"] is None
    assert app.error[0].value == "Could not clear the browser session. Your sign-in was revoked."
