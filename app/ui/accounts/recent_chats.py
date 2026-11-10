"""Personal thread management backed by the validated account session."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

import streamlit as st

from app.accounts.conversation import ConversationError, Principal
from app.ui.language import shared_language, translate

if TYPE_CHECKING:
    from app.accounts.conversation import AccountBackend, OpenedConversation
    from app.accounts.storage.models import ChatThread
    from app.ui.accounts.panels import BrowserSession
    from app.ui.accounts.persistent_chat import ThreadView

_STATE = "account_recent_chats"
_EXPIRED = {"UNAUTHENTICATED", "IDENTITY_CHANGED"}
_LABELS = {
    "Recent Chats": "最近聊天",
    "New Chat": "新建聊天",
    "New conversation": "新对话",
    "Open Chat": "打开聊天",
    "Open conversation": "当前聊天",
    "Rename Chat": "重命名聊天",
    "Delete Chat": "删除聊天",
    "Rename": "重命名",
    "Delete": "删除",
    "Chat options": "聊天选项",
    "Delete this chat?": "删除这段聊天？",
    "This removes the conversation and its saved messages.": "这将删除此聊天及其已保存的消息。",
    "Chat title": "聊天标题",
    "Save title": "保存标题",
    "Cancel": "取消",
    "Confirm deletion": "确认删除",
    "Permanently delete this conversation?": "要永久删除这段聊天吗？",
    "Loading chats…": "正在加载聊天…",
    "Creating chat…": "正在创建聊天…",
    "Opening chat…": "正在打开聊天…",
    "Saving title…": "正在保存标题…",
    "Deleting chat…": "正在删除聊天…",
    "No conversations yet.": "暂无聊天记录。",
    "Sign in to view your chats.": "登录后即可查看聊天记录。",
    "Your session ended. Sign in again.": "会话已结束，请重新登录。",
    "You do not have access to this conversation or workspace.": "你无权访问此聊天或工作区。",
    "This conversation needs verification before it can be opened.": (
        "这段聊天需要核实后才能打开。"
    ),
    "This conversation is no longer available.": "这段聊天已不可用。",
    "Enter a chat title of 1 to 200 characters.": "请输入 1 到 200 个字符的聊天标题。",
    "The change may have been saved. Check your chat list before trying again.": (
        "更改可能已保存。请先查看聊天列表，再决定是否重试。"
    ),
    "Could not load chats. Please try again.": "无法加载聊天记录，请重试。",
    "Could not complete the chat action. Please try again.": "无法完成聊天操作，请重试。",
}


@dataclass
class _HistoryState:
    owner: Principal
    widget_prefix: str = field(default_factory=lambda: f"{_STATE}_{uuid4().hex}_")
    opened: OpenedConversation | ThreadView | None = None
    editing: str | None = None
    editing_thread: str | None = None
    initialized: bool = False

    def select(self, opened: OpenedConversation | ThreadView | None) -> None:
        self.opened = opened
        self.editing = None
        self.editing_thread = None


def sync_history_owner(principal: Principal | None) -> None:
    """Remove history and any widget drafts when the verified login changes."""
    history = st.session_state.get(_STATE)
    if history is None or history.owner == principal:
        return
    for key in list(st.session_state):
        if key.startswith(history.widget_prefix):
            st.session_state.pop(key, None)
    st.session_state.pop(_STATE, None)


def _label(text: str, language: str) -> str:
    return _LABELS[text] if language == "zh" else text


def _error_code(error: Exception) -> str:
    return error.code if isinstance(error, ConversationError) else ""


def _show_error(error: Exception, fallback: str, language: str) -> None:
    code = _error_code(error)
    if code in _EXPIRED:
        st.warning(_label("Your session ended. Sign in again.", language))
        return
    message = {
        "FORBIDDEN": "You do not have access to this conversation or workspace.",
        "NOT_FOUND": "This conversation is no longer available.",
        "RECONCILIATION_REQUIRED": "This conversation needs verification before it can be opened.",
        "STORAGE_OUTCOME_UNKNOWN": (
            "The change may have been saved. Check your chat list before trying again."
        ),
    }.get(code, fallback)
    st.error(_label(message, language))


def _manage_open_chat(
    backend: AccountBackend,
    handle: str,
    history: _HistoryState,
    thread: ChatThread,
    language: str,
    verify_identity: Callable[[], None],
) -> None:
    prefix = f"{history.widget_prefix}{thread.thread_id}_"
    st.caption(_label("Open conversation", language))
    st.text(thread.title)
    rename, delete = st.columns(2)
    if rename.button(_label("Rename Chat", language), key=f"{prefix}rename"):
        history.editing = "rename"
    if delete.button(_label("Delete Chat", language), key=f"{prefix}delete"):
        history.editing = "delete"

    if history.editing == "rename":
        with st.form(f"{prefix}rename_form"):
            title = st.text_input(
                _label("Chat title", language),
                value=thread.title,
                max_chars=200,
                key=f"{prefix}title",
            )
            save = st.form_submit_button(_label("Save title", language), type="primary")
        if save:
            if not title.strip() or len(title) > 200:
                st.error(_label("Enter a chat title of 1 to 200 characters.", language))
                return
            with st.spinner(_label("Saving title…", language)):
                verify_identity()
                backend.rename_thread(handle, thread.thread_id, title)
                verify_identity()
            history.editing = None
            st.rerun()

    if history.editing == "delete":
        st.warning(_label("Permanently delete this conversation?", language))
        confirm, cancel = st.columns(2)
        if cancel.button(_label("Cancel", language), key=f"{prefix}cancel_delete"):
            history.editing = None
            st.rerun()
        if confirm.button(
            _label("Confirm deletion", language), key=f"{prefix}confirm_delete", type="primary"
        ):
            with st.spinner(_label("Deleting chat…", language)):
                verify_identity()
                backend.delete_thread(handle, thread.thread_id)
                verify_identity()
            history.select(None)
            st.rerun()


def _render_threads(
    backend: AccountBackend,
    handle: str,
    history: _HistoryState,
    threads: list[ChatThread],
    language: str,
    open_thread: Callable[[str, str], OpenedConversation | ThreadView],
    verify_identity: Callable[[], None],
    *,
    compact=False,
    new_chat_panel=None,
    reuse_current=None,
    chat_label=None,
    can_reuse=None,
) -> OpenedConversation | ThreadView | None:
    prefix = history.widget_prefix
    with (new_chat_panel or nullcontext)():
        create = st.button(
            ("＋  " + translate("New conversation", language))
            if compact
            else _label("New Chat", language),
            key=f"{prefix}new",
            type="primary" if compact else "secondary",
            use_container_width=compact,
        )
    if create or (compact and not history.initialized):
        # Only initialize once per verified login. Unknown writes are not auto-retried.
        history.initialized = True
        if compact:
            st.session_state["ui_page"] = "copilot"
        reusable_id = reuse_current() if reuse_current is not None else None
        if compact:
            preferred = history.opened.thread_id if history.opened is not None else reusable_id
            candidates = sorted(threads, key=lambda thread: thread.thread_id != preferred)
            for candidate in candidates:
                if can_reuse is not None and not can_reuse(candidate.thread_id):
                    continue
                verify_identity()
                snapshot = backend.open_thread(handle, candidate.thread_id)
                verify_identity()
                state = getattr(snapshot, "session", getattr(snapshot, "agent_state", None))
                if (
                    snapshot.thread_id != candidate.thread_id
                    or snapshot.messages
                    or snapshot.pending is not None
                    or getattr(getattr(state, "state", None), "tasks", [])
                    or getattr(state, "global_issues", [])
                ):
                    continue
                opened = open_thread(handle, candidate.thread_id)
                verify_identity()
                # A concurrent browser may have sent a message after our read.
                if opened.messages or opened.pending is not None:
                    continue
                history.select(opened)
                st.session_state["workflow"] = None
                st.session_state["workflow_panel_expanded"] = True
                st.rerun()
        history.select(None)
        with st.spinner(_label("Creating chat…", language)):
            verify_identity()
            thread = backend.create_thread(handle, _label("New conversation", language))
        with st.spinner(_label("Opening chat…", language)):
            verify_identity()
            opened = open_thread(handle, thread.thread_id)
            verify_identity()
            history.select(opened)
        st.rerun()

    if not threads:
        (st.caption if compact else st.info)(_label("No conversations yet.", language))

    by_id = {thread.thread_id: thread for thread in threads}
    if history.opened is not None and history.opened.thread_id not in by_id:
        history.select(None)
    with st.container(height=250, border=False, key="recent_chats") if compact else nullcontext():
        for thread in threads:
            if compact:
                selected = (
                    history.opened is not None and history.opened.thread_id == thread.thread_id
                )
                title, hover = chat_label(thread) if chat_label else (thread.title, thread.title)
                with st.container(key=f"chat_row_{thread.thread_id}"):
                    clicked = st.button(
                        ("● " if selected else "◦ ") + title,
                        key=f"{prefix}{thread.thread_id}_open",
                        help=hover,
                        use_container_width=True,
                    )
                    menu_key = f"{prefix}{thread.thread_id}_menu"
                    if history.editing_thread is not None:
                        st.session_state[menu_key] = False
                    with st.popover(
                        "⋯",
                        help=_label("Chat options", language) + " · " + title,
                        key=menu_key,
                        type="tertiary",
                        on_change="rerun",
                    ):
                        with st.container(key=f"chat_menu_contents_{thread.thread_id}"):
                            for action, label, icon in (
                                ("rename", "Rename", "edit"),
                                ("delete", "Delete", "delete"),
                            ):
                                if st.button(
                                    _label(label, language),
                                    icon=f":material/{icon}:",
                                    key=f"{prefix}{thread.thread_id}_{action}",
                                    width="stretch",
                                ):
                                    verify_identity()
                                    history.editing, history.editing_thread = (
                                        action,
                                        thread.thread_id,
                                    )
                                    st.rerun()
            else:
                title, action = st.columns([3, 2])
                title.text(thread.title)
                clicked = action.button(
                    _label("Open Chat", language), key=f"{prefix}{thread.thread_id}_open"
                )
            if clicked:
                history.select(None)
                with st.spinner(_label("Opening chat…", language)):
                    verify_identity()
                    opened = open_thread(handle, thread.thread_id)
                    verify_identity()
                    history.select(opened)
                if compact:
                    st.session_state["ui_page"] = "copilot"
                    st.rerun()

    if history.opened is not None and not compact:
        _manage_open_chat(
            backend, handle, history, by_id[history.opened.thread_id], language, verify_identity
        )
    return history.opened


def render_recent_chats(
    backend: AccountBackend,
    session: BrowserSession | None = None,
    *,
    expected_owner: Principal | None = None,
    open_thread: Callable[[str, str], OpenedConversation | ThreadView] | None = None,
    on_session_error: Callable[[], None] | None = None,
    compact=False,
    new_chat_panel=None,
    reuse_current=None,
    chat_label=None,
    can_reuse=None,
) -> OpenedConversation | ThreadView | None:
    """Render personal threads, optionally opening them through the workspace adapter."""
    language = shared_language(st.session_state)
    if compact:
        st.markdown(
            '<div class="sc-side-rule sc-recent-rule"></div><div class="sc-side-title">'
            + ("RECENT CHATS" if language == "en" else "最近会话")
            + "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.subheader(_label("Recent Chats", language))

    try:
        handle = session.read() if session is not None else None
        principal = backend.current_user(handle) if handle else None
        if expected_owner is not None and principal != expected_owner:
            raise ConversationError("IDENTITY_CHANGED")
    except Exception as error:
        sync_history_owner(None)
        if on_session_error is not None:
            on_session_error()
        _show_error(error, "Could not load chats. Please try again.", language)
        return None
    sync_history_owner(principal)
    if not handle or principal is None:
        st.info(_label("Sign in to view your chats.", language))
        return None

    def verify_identity() -> None:
        if expected_owner is None:
            return
        try:
            current = session.read() if session is not None else None
            current_owner = backend.current_user(current) if current == handle else None
        except Exception:
            raise ConversationError("IDENTITY_CHANGED") from None
        if current_owner != expected_owner:
            raise ConversationError("IDENTITY_CHANGED")

    history = st.session_state.get(_STATE)
    if history is None:
        history = _HistoryState(principal)
        st.session_state[_STATE] = history

    try:
        with st.spinner(_label("Loading chats…", language)):
            threads = backend.list_threads(handle)
            verify_identity()
        if any(
            (thread.user_id, thread.workspace_id) != (principal.user_id, principal.workspace_id)
            for thread in threads
        ):
            raise ConversationError("FORBIDDEN")
    except Exception as error:
        sync_history_owner(None)
        if on_session_error is not None and _error_code(error) in _EXPIRED:
            on_session_error()
        _show_error(error, "Could not load chats. Please try again.", language)
        return None

    private_panel = st.empty()
    try:
        with private_panel.container():
            return _render_threads(
                backend,
                handle,
                history,
                threads,
                language,
                open_thread or backend.open_thread,
                verify_identity,
                compact=compact,
                new_chat_panel=new_chat_panel,
                reuse_current=reuse_current,
                chat_label=chat_label,
                can_reuse=can_reuse,
            )
    except Exception as error:
        code = _error_code(error)
        if code in _EXPIRED or code == "FORBIDDEN":
            private_panel.empty()
            sync_history_owner(None)
            if code in _EXPIRED and on_session_error is not None:
                on_session_error()
        elif code == "NOT_FOUND":
            private_panel.empty()
            history.select(None)
        elif code == "STORAGE_OUTCOME_UNKNOWN":
            history.editing = None
        _show_error(error, "Could not complete the chat action. Please try again.", language)
        return None


def render_chat_management_dialog(
    backend, session, expected_owner, *, chat_label=None, on_session_error=None
):
    """Render outside the clipped sidebar; reauthorize every edit and confirmation."""
    history = st.session_state.get(_STATE)
    if history is None or history.owner != expected_owner or not history.editing_thread:
        return False
    language = shared_language(st.session_state)
    target_id, action = history.editing_thread, history.editing

    from app.ui.dialog_layout import mount_dialog_layout

    mount_dialog_layout(language)

    def dismiss():
        history.editing = history.editing_thread = None

    def authorize():
        handle = session.read() if session is not None else None
        if not handle or backend.current_user(handle) != expected_owner:
            sync_history_owner(None)
            raise ConversationError("IDENTITY_CHANGED")
        return handle

    @st.dialog(
        _label("Rename Chat" if action == "rename" else "Delete this chat?", language),
        on_dismiss=dismiss,
    )
    def editor():
        st.markdown(
            '<span class="sc-resizable-dialog-marker sc-account-dialog-marker" '
            'aria-hidden="true"></span>',
            unsafe_allow_html=True,
        )
        try:
            handle = authorize()
            threads = backend.list_threads(handle)
            authorize()
            thread = next((item for item in threads if item.thread_id == target_id), None)
            if thread is None:
                raise ConversationError("NOT_FOUND")
            if (thread.user_id, thread.workspace_id) != (
                expected_owner.user_id,
                expected_owner.workspace_id,
            ):
                raise ConversationError("FORBIDDEN")
            title = chat_label(thread)[0] if chat_label is not None else thread.title
            prefix = f"{history.widget_prefix}{target_id}_dialog_"
            if action == "rename":
                with st.form(prefix + "rename"):
                    value = st.text_input(
                        _label("Chat title", language),
                        value=title,
                        max_chars=200,
                        key=prefix + "title",
                    )
                    save = st.form_submit_button(_label("Save title", language), type="primary")
                if save:
                    if not value.strip() or len(value) > 200:
                        st.error(_label("Enter a chat title of 1 to 200 characters.", language))
                        return
                    backend.rename_thread(authorize(), target_id, value)
                    authorize()
                    dismiss()
                    st.rerun()
            else:
                st.markdown("**" + title.replace("*", "\\*") + "**")
                st.write(_label("This removes the conversation and its saved messages.", language))
                confirm, cancel = st.columns(2)
                if confirm.button(
                    _label("Confirm deletion", language), key=prefix + "confirm", type="primary"
                ):
                    backend.delete_thread(authorize(), target_id)
                    authorize()
                    if history.opened is not None and history.opened.thread_id == target_id:
                        history.select(None)
                        history.initialized = False
                    dismiss()
                    st.rerun()
                if cancel.button(_label("Cancel", language), key=prefix + "cancel"):
                    dismiss()
                    st.rerun()
        except Exception as error:
            dismiss()
            if _error_code(error) in _EXPIRED:
                if on_session_error is not None:
                    on_session_error()
                st.rerun()
            _show_error(error, "Could not complete the chat action. Please try again.", language)

    editor()
    return True
