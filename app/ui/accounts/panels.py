"""Account controls backed by P4 and an injected browser session adapter."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
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
PanelContext = Callable[[], AbstractContextManager[None]]
_LABELS = {
    "Account": "账号",
    "Sign in": "登录",
    "Register": "注册",
    "Sign out": "退出登录",
    "Username": "用户名",
    "Password": "密码",
    "Enter your username": "请输入用户名",
    "Enter your password": "请输入密码",
    "Use 6–15 characters; avoid your username and common passwords.": (
        "密码需为 6–15 个字符，且不能与用户名相同或使用常见弱密码。"
    ),
    "Welcome back": "欢迎回来",
    "Sign in to access your factory workspace": "登录以进入你的工厂工作区",
    "Your factory operations, all in one place.": "在一个地方管理工厂运营。",
    "Return to sign in": "返回登录",
    "Signed in": "已登录",
    "Signed in as {username}": "已登录：{username}",
    "Enter your username and password.": "请输入用户名和密码。",
    "Demo account. Chat history is shared.": "演示账号。聊天记录由使用者共享。",
    "Account created. Sign in to continue.": "注册成功，请登录以继续。",
    "Your session ended. Sign in again.": "会话已结束，请重新登录。",
    "Secure browser sign-in is not configured.": "尚未配置安全的浏览器登录方式。",
    "Could not update the browser session. Please try again.": ("无法更新浏览器登录会话，请重试。"),
    "Could not clear the browser session. Your sign-in was revoked.": (
        "无法清除浏览器会话，服务端登录已撤销。"
    ),
    "Check the username requirements and try again.": "请检查用户名是否符合要求后重试。",
    "This username is unavailable.": "此用户名不可用。",
    "The username or password is incorrect.": "用户名或密码不正确。",
    "Too many attempts. Please try again later.": "尝试次数过多，请稍后重试。",
    "This account cannot access the workspace.": "此账号无法访问该工作区。",
    "The account service is unavailable. Please try again later.": "账号服务暂不可用，请稍后重试。",
}
_ERROR_LABELS = {
    "INVALID_USERNAME": "Check the username requirements and try again.",
    "INVALID_PASSWORD": "Use 6–15 characters; avoid your username and common passwords.",
    "USERNAME_TAKEN": "This username is unavailable.",
    "INVALID_CREDENTIALS": "The username or password is incorrect.",
    "RATE_LIMITED": "Too many attempts. Please try again later.",
    "FORBIDDEN": "This account cannot access the workspace.",
}


class BrowserSession(Protocol):
    """Server-side login handle transport supplied by the host."""

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


@contextmanager
def auth_page(render_preferences: Callable[[], None], *, notice: str | None = None):
    """Place account controls in the dedicated signed-out view."""
    language = shared_language(st.session_state)
    st.html(Path(__file__).with_name("auth_page.css"))
    with st.container(key="account_auth_page", horizontal_alignment="center"):
        with st.container(
            horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
        ):
            st.html(
                '<div class="sc-brand"><span class="sc-mark" aria-hidden="true">SC</span>'
                "<div><strong>SweaterCo</strong><small>OPERATIONS CO-PILOT</small></div></div>"
            )
            with st.container(
                key="account_auth_preferences", horizontal=True, width="content", gap="small"
            ):
                render_preferences()

        with st.container(key="account_auth_content", width=460):
            st.markdown(":material/lock:", text_alignment="center")
            st.markdown(
                "# " + _label("Welcome back", language), text_alignment="center", anchors=False
            )
            st.markdown(
                _label("Sign in to access your factory workspace", language),
                text_alignment="center",
            )
            with st.container(key="account_auth_card", border=True):
                if notice is not None:
                    st.info(notice)
                yield
            st.markdown(
                _label("Your factory operations, all in one place.", language),
                text_alignment="center",
            )


def auth_retry() -> None:
    st.button(_label("Return to sign in", shared_language(st.session_state)))


def _credentials(
    form: str,
    action: str,
    language: str,
    *,
    login_defaults: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    default_username, default_password = login_defaults or ("", "")
    with st.form(f"account_{form}", clear_on_submit=True, border=False):
        if login_defaults:
            st.caption(_label("Demo account. Chat history is shared.", language))
        username = st.text_input(
            _label("Username", language),
            value=default_username,
            placeholder=_label("Enter your username", language),
            key=f"account_{form}_username",
        )
        password = st.text_input(
            _label("Password", language),
            value=default_password,
            type="password",
            placeholder=_label("Enter your password", language),
            key=f"account_{form}_password",
        )
        if form == "register":
            st.caption(
                _label(
                    "Use 6–15 characters; avoid your username and common passwords.",
                    language,
                )
            )
        submitted = st.form_submit_button(_label(action, language), type="primary", width="stretch")
    if not submitted:
        return None
    if not username.strip() or not password:
        st.error(_label("Enter your username and password.", language))
        return None
    return username, password


def _render_login(
    backend: AccountBackend,
    session: BrowserSession,
    language: str,
    *,
    login_defaults: tuple[str, str] | None = None,
) -> None:
    credentials = _credentials("login", "Sign in", language, login_defaults=login_defaults)
    if credentials is None:
        return
    try:
        login = backend.login(*credentials)
    except Exception as error:
        _show_error(error, language)
        return
    try:
        session.set_cookie(login.cookie)
    except Exception:
        st.error(_label("Could not update the browser session. Please try again.", language))
        return
    principal = Principal(login.user_id, login.workspace_id, login.session_id)
    st.session_state[_DISPLAY_IDENTITY] = (principal, login.username)
    st.rerun()


def _render_signed_out(
    backend: AccountBackend,
    session: BrowserSession,
    language: str,
    *,
    allow_registration: bool,
    login_defaults: tuple[str, str] | None,
) -> None:
    if not allow_registration:
        _render_login(backend, session, language, login_defaults=login_defaults)
        return

    login_tab, register_tab = st.tabs([_label("Sign in", language), _label("Register", language)])
    with login_tab:
        _render_login(backend, session, language)

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
    signed_out: PanelContext,
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
        if isinstance(error, ConversationError) and error.code in _EXPIRED:
            st.session_state.pop(_DISPLAY_IDENTITY, None)
            _sync_identity(None, on_identity)
            with signed_out():
                _show_error(error, language)
                auth_retry()
            return None
        _show_error(error, language)
        return principal

    st.session_state.pop(_DISPLAY_IDENTITY, None)
    _sync_identity(None, on_identity)
    try:
        session.clear_cookie(deletion)
    except Exception:
        with signed_out():
            st.error(
                _label("Could not clear the browser session. Your sign-in was revoked.", language)
            )
            auth_retry()
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
    signed_in: PanelContext | None = None,
    signed_out: PanelContext | None = None,
    allow_registration: bool = True,
    login_defaults: tuple[str, str] | None = None,
) -> Principal | None:
    """Render controls and return only the identity verified by the backend."""
    language = shared_language(st.session_state)
    in_panel = signed_in or nullcontext
    out_panel = signed_out or nullcontext
    if signed_out is None:
        st.subheader(_label("Account", language))
    if session is None:
        _sync_identity(None, on_identity)
        st.session_state.pop(_DISPLAY_IDENTITY, None)
        with out_panel():
            st.info(_label("Secure browser sign-in is not configured.", language))
        return None

    try:
        handle = session.read()
    except Exception:
        _sync_identity(None, on_identity)
        st.session_state.pop(_DISPLAY_IDENTITY, None)
        with out_panel():
            st.error(_label("Could not update the browser session. Please try again.", language))
        return None
    session_error = None
    if handle:
        try:
            principal = backend.current_user(handle)
        except Exception as error:
            session_error = error
        else:
            with in_panel():
                if signed_in is not None:
                    st.subheader(_label("Account", language))
                return _render_signed_in(
                    backend, session, handle, principal, language, on_identity, out_panel
                )

    _sync_identity(None, on_identity)
    st.session_state.pop(_DISPLAY_IDENTITY, None)
    with out_panel():
        if session_error is not None:
            _show_error(session_error, language)
        _render_signed_out(
            backend,
            session,
            language,
            allow_registration=allow_registration,
            login_defaults=login_defaults,
        )
    return None
