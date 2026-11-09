"""Account controls backed by P4 and an injected browser session adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

import streamlit as st

from app.accounts.conversation import ConversationError, Principal
from app.ui.accounts.recent_chats import sync_history_owner
from app.ui.language import shared_language

if TYPE_CHECKING:
    from app.accounts.conversation import AccountBackend
    from app.accounts.session.models import SessionCookie

_DISPLAY_IDENTITY = "account_display_identity"
_EXPIRED = {"UNAUTHENTICATED", "IDENTITY_CHANGED"}
_LABELS = {
    "Account": "账号",
    "Sign in": "登录",
    "Register": "注册",
    "Sign out": "退出登录",
    "Username": "用户名",
    "Password": "密码",
    "Signed in": "已登录",
    "Signed in as {username}": "已登录：{username}",
    "Enter your username and password.": "请输入用户名和密码。",
    "Account created. Sign in to continue.": "注册成功，请登录以继续。",
    "Your session ended. Sign in again.": "会话已结束，请重新登录。",
    "Secure browser sign-in is not configured.": "尚未配置安全的浏览器登录方式。",
    "Could not update the browser session. Please try again.": ("无法更新浏览器登录会话，请重试。"),
    "Could not clear the browser session. Your sign-in was revoked.": (
        "无法清除浏览器会话，服务端登录已撤销。"
    ),
    "Check the username requirements and try again.": "请检查用户名是否符合要求后重试。",
    "Check the password requirements and try again.": "请检查密码是否符合要求后重试。",
    "This username is unavailable.": "此用户名不可用。",
    "The username or password is incorrect.": "用户名或密码不正确。",
    "Too many attempts. Please try again later.": "尝试次数过多，请稍后重试。",
    "This account cannot access the workspace.": "此账号无法访问该工作区。",
    "The account service is unavailable. Please try again later.": "账号服务暂不可用，请稍后重试。",
}
_ERROR_LABELS = {
    "INVALID_USERNAME": "Check the username requirements and try again.",
    "INVALID_PASSWORD": "Check the password requirements and try again.",
    "USERNAME_TAKEN": "This username is unavailable.",
    "INVALID_CREDENTIALS": "The username or password is incorrect.",
    "RATE_LIMITED": "Too many attempts. Please try again later.",
    "FORBIDDEN": "This account cannot access the workspace.",
}


class BrowserSession(Protocol):
    """Server-side cookie transport supplied by the host, separate from account logic."""

    def read(self) -> str | None:
        """Read the current browser session handle for this request."""
        ...

    def set_cookie(self, cookie: SessionCookie) -> None:
        """Apply login instructions and make the new handle readable on the next run."""
        ...

    def clear_cookie(self, cookie: SessionCookie) -> None:
        """Apply logout instructions and clear the handle for the next run."""
        ...


def _label(text: str, language: str) -> str:
    return _LABELS[text] if language == "zh" else text


def _show_error(error: Exception, language: str) -> None:
    code = error.code if isinstance(error, ConversationError) else ""
    if code in _EXPIRED:
        st.warning(_label("Your session ended. Sign in again.", language))
    else:
        fallback = "The account service is unavailable. Please try again later."
        st.error(_label(_ERROR_LABELS.get(code, fallback), language))


def _credentials(form: str, action: str, language: str) -> tuple[str, str] | None:
    with st.form(f"account_{form}", clear_on_submit=True):
        username = st.text_input(_label("Username", language), key=f"account_{form}_username")
        password = st.text_input(
            _label("Password", language), type="password", key=f"account_{form}_password"
        )
        submitted = st.form_submit_button(_label(action, language), type="primary")
    if not submitted:
        return None
    if not username.strip() or not password:
        st.error(_label("Enter your username and password.", language))
        return None
    return username, password


def _render_signed_out(backend: AccountBackend, session: BrowserSession, language: str) -> None:
    login_tab, register_tab = st.tabs([_label("Sign in", language), _label("Register", language)])
    with login_tab:
        credentials = _credentials("login", "Sign in", language)
        if credentials is not None:
            try:
                login = backend.login(*credentials)
            except Exception as error:
                _show_error(error, language)
            else:
                try:
                    session.set_cookie(login.cookie)
                except Exception:
                    st.error(
                        _label("Could not update the browser session. Please try again.", language)
                    )
                    return
                principal = Principal(login.user_id, login.workspace_id, login.session_id)
                st.session_state[_DISPLAY_IDENTITY] = (principal, login.username)
                st.rerun()

    with register_tab:
        credentials = _credentials("register", "Register", language)
        if credentials is not None:
            try:
                backend.register(*credentials)
            except Exception as error:
                _show_error(error, language)
            else:
                st.success(_label("Account created. Sign in to continue.", language))


def _render_signed_in(
    backend: AccountBackend,
    session: BrowserSession,
    handle: str,
    principal: Principal,
    language: str,
    on_identity: Callable[[Principal | None], None] | None,
) -> Principal | None:
    display = st.session_state.get(_DISPLAY_IDENTITY)
    if display and display[0] == principal:
        st.text(_label("Signed in as {username}", language).format(username=display[1]))
    else:
        st.session_state.pop(_DISPLAY_IDENTITY, None)
        st.text(_label("Signed in", language))

    _sync_identity(principal, on_identity)
    key = f"account_logout_{principal.session_id}" if on_identity else "account_logout"
    if not st.button(_label("Sign out", language), key=key):
        return principal
    try:
        deletion = backend.logout(handle)
    except Exception as error:
        _show_error(error, language)
        if isinstance(error, ConversationError) and error.code in _EXPIRED:
            st.session_state.pop(_DISPLAY_IDENTITY, None)
            _sync_identity(None, on_identity)
            return None
        return principal

    st.session_state.pop(_DISPLAY_IDENTITY, None)
    _sync_identity(None, on_identity)
    try:
        session.clear_cookie(deletion)
    except Exception:
        st.error(_label("Could not clear the browser session. Your sign-in was revoked.", language))
        return None
    st.rerun()


def _sync_identity(
    principal: Principal | None, on_identity: Callable[[Principal | None], None] | None
) -> None:
    sync_history_owner(principal)
    if on_identity is not None:
        on_identity(principal)


def render_account_panel(
    backend: AccountBackend,
    session: BrowserSession | None = None,
    *,
    on_identity: Callable[[Principal | None], None] | None = None,
) -> Principal | None:
    """Render controls and return only the identity verified by the backend."""
    language = shared_language(st.session_state)
    st.subheader(_label("Account", language))
    if session is None:
        _sync_identity(None, on_identity)
        st.session_state.pop(_DISPLAY_IDENTITY, None)
        st.info(_label("Secure browser sign-in is not configured.", language))
        return None

    try:
        handle = session.read()
    except Exception:
        _sync_identity(None, on_identity)
        st.session_state.pop(_DISPLAY_IDENTITY, None)
        st.error(_label("Could not update the browser session. Please try again.", language))
        return None
    if handle:
        try:
            principal = backend.current_user(handle)
        except Exception as error:
            _show_error(error, language)
        else:
            return _render_signed_in(backend, session, handle, principal, language, on_identity)

    _sync_identity(None, on_identity)
    st.session_state.pop(_DISPLAY_IDENTITY, None)
    _render_signed_out(backend, session, language)
    return None
