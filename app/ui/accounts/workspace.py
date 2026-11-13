"""Streamlit wiring for the personal workspace and its isolated view state."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import streamlit as st

from app.accounts.conversation import ConversationError
from app.accounts.conversation.service import MAX_TEXT_CHARACTERS
from app.ui.accounts.panels import render_account_panel
from app.ui.accounts.persistent_chat import PersistentChat
from app.ui.accounts.real_backend import RealAccountMode
from app.ui.accounts.recent_chats import sync_history_owner
from app.ui.language import shared_language

if TYPE_CHECKING:
    from app.accounts.conversation import AccountBackend, Principal
    from app.agent.contracts import AgentReply
    from app.ui.accounts.panels import BrowserSession, PanelContext

_STATE = "account_workspace"
_SESSION_ERRORS = {"UNAUTHENTICATED", "IDENTITY_CHANGED"}
_CONTEXT_KEYS = (
    "threads",
    "active_thread",
    "next_thread",
    "workflow",
    "workflow_panel_expanded",
    "chat_prompt",
    "pending_chat",
    "pending_speech",
    "handled_recordings",
    "chat_notice",
    "text_translations",
    "proactive_ui",
    "proactive_scope",
    "ui_page",
    "active_composer",
)
_LABELS = {
    "Account service is not configured. Contact the app maintainer.": (
        "账号服务尚未配置，请联系应用负责人。"
    ),
    "Evidence CSV export is unavailable for private chats.": "私人聊天暂不提供证据 CSV 下载。",
    "Open or create a chat in Recent Chats to start.": "请在最近聊天中打开或新建聊天。",
    "Loading saved chat…": "正在读取已保存的聊天…",
    "Sending and saving your message…": "正在发送并保存消息…",
    "A turn is processing. Refresh the chat to check its status.": (
        "有一条消息正在处理。请重新读取聊天以查看状态。"
    ),
    "The previous turn needs verification. Sending is paused for this chat.": (
        "上一条消息的结果待核实。此聊天已暂停发送。"
    ),
    "The save outcome is unknown. Refresh the chat to check the stored history.": (
        "保存结果未知。请重新读取聊天以核对已保存的记录。"
    ),
    "The result was saved. Refresh the chat to load it.": "结果已保存，请重新读取聊天。",
    "Storage is unavailable. You can retry the same message.": (
        "存储暂不可用。你可以重试同一条消息。"
    ),
    "The chat changed. Refresh it before sending again.": "聊天已变更。请重新读取后再发送。",
    "Could not update this chat. Please try again.": "无法更新此聊天，请重试。",
    "Your session ended. Sign in again.": "会话已结束，请重新登录。",
    "Use {limit:,} characters or fewer. Your text is still in the input box.": (
        "消息不能超过 {limit:,} 个字符。原文已保留在输入框中。"
    ),
    "Refresh chat": "重新读取聊天",
    "Retry same message": "重试同一条消息",
    "Session-only workflow preview · Not saved to chat history": (
        "仅本次会话可见的工作流预览 · 未保存到聊天记录"
    ),
}


def label(text: str) -> str:
    return _LABELS[text] if shared_language(st.session_state) == "zh" else text


@dataclass(frozen=True)
class AccountTestMode:
    """Explicit injection point for an offline backend and fake browser session."""

    backend: AccountBackend
    session: BrowserSession
    chat_ready: bool = False


class AccountWorkspace:
    def __init__(self, mode: AccountTestMode | RealAccountMode, backend: AccountBackend) -> None:
        self.mode = mode
        self.backend = backend
        self.chat = PersistentChat(backend)
        self.owner: Principal | None = None
        self.scope = uuid4().hex
        self.thread = {"id": None, "messages": []}
        self.thread_drafts: dict[str, dict[str, Any]] = {}
        self.proactive_application = None
        self.creation_needs_verification = False
        self.title_snapshots = {}
        self._snapshot = None
        self.preview: tuple[str, dict[str, Any], AgentReply, str] | None = None
        self.notice: str | None = None

    def widget(self, name: str) -> str:
        return f"account_chat_{self.scope}_{name}"

    def sync_owner(self, owner: Principal | None) -> None:
        if owner == self.owner and owner is not None:
            return
        self.chat.clear()
        if owner is None:
            st.session_state.pop("account_display_identity", None)
        self.owner = owner
        prefix = self.widget("")
        self.scope = uuid4().hex
        for key in list(st.session_state):
            if key.startswith(prefix) or key.startswith("evidence_" + prefix):
                st.session_state.pop(key, None)
        if self.thread["id"] is not None:
            st.session_state.pop(f"follow_bridge_{self.thread['id']}", None)
        for key in _CONTEXT_KEYS:
            st.session_state.pop(key, None)
        self.thread = {"id": None, "messages": []}
        self.thread_drafts = {}
        self.proactive_application = None
        self.creation_needs_verification = False
        self.title_snapshots = {}
        for key in list(st.session_state):
            if key.startswith("pa_field_"):
                st.session_state.pop(key, None)
        self._snapshot = None
        self.preview = None
        self.notice = None

    def render_auth(self, *, signed_in: PanelContext, signed_out: PanelContext) -> bool:
        @contextmanager
        def account_sidebar():
            with signed_in():
                yield

        return (
            render_account_panel(
                self.backend,
                self.mode.session,
                on_identity=self.sync_owner,
                signed_in=account_sidebar,
                signed_out=signed_out,
                allow_registration=(
                    not isinstance(self.mode, RealAccountMode) or self.mode.allow_registration
                ),
                login_defaults=(
                    self.mode.login_defaults if isinstance(self.mode, RealAccountMode) else None
                ),
            )
            is not None
        )

    def validate_owner(self) -> bool:
        """Validate the request before private rendering without adding another UI panel."""
        try:
            handle = self.mode.session.read() if self.mode.session is not None else None
            owner = self.backend.current_user(handle) if handle else None
        except Exception:
            owner = None
        self.sync_owner(owner)
        return owner is not None

    def current_handle(self) -> str | None:
        """Check the current request before callbacks or private content render."""
        if self.owner is None:
            return None
        try:
            session = self.mode.session
            handle = session.read() if session is not None else None
            principal = self.chat.sync_session(handle)
        except Exception:
            principal = None
        if principal != self.owner:
            sync_history_owner(None)
            self.sync_owner(None)
            return None
        return handle

    def sync_view(self) -> None:
        view = self.chat.selected
        if view is self._snapshot:
            return
        self._snapshot = view
        thread_id = view.thread_id if view is not None else None
        switched = self.thread["id"] != thread_id
        if switched:
            if self.thread["id"] is not None:
                self.thread["workflow_preview"] = self.preview
                self.thread_drafts[self.thread["id"]] = self.thread
            st.session_state.pop(f"follow_bridge_{self.thread['id']}", None)
            self.thread = self.thread_drafts.get(thread_id, {"id": thread_id, "messages": []})
            self.preview = self.thread.get("workflow_preview")
            st.session_state["workflow"] = None
        self.thread["messages"] = (
            [
                {
                    "role": message.role,
                    "content": message.text,
                    "reply": message.reply,
                    "ui_reply_id": message.message_id,
                }
                for message in view.messages
            ]
            if view is not None
            else []
        )
        if view is not None:
            self.title_snapshots[view.thread_id] = view
            self.thread["agent_session"] = view.agent_state
            self.creation_needs_verification = False
            for index in range(0, len(self.thread["messages"]) - 1, 2):
                answer = self.thread["messages"][index + 1]
                reply = answer.get("reply")
                request = reply.metadata.get("ui_workflow_request") if reply is not None else None
                if isinstance(request, dict):
                    self.thread["messages"][index]["ui_workflow_request"] = request
                    answer["origin"] = "Quick Workflows"
            self.thread["messages"] = _visible_messages(self.thread["messages"])
            if switched:
                st.session_state["workflow_panel_expanded"] = not bool(self.thread["messages"])
                latest = next(
                    (
                        message
                        for message in reversed(self.thread["messages"])
                        if message["role"] == "assistant"
                    ),
                    None,
                )
                if latest is None:
                    self.thread.pop("selected_reply_id", None)
                    self.thread.pop("scroll_to_answer", None)
                else:
                    identity = latest["ui_reply_id"]
                    self.thread["selected_reply_id"] = identity
                    self.thread["scroll_to_answer"] = identity

    def set_preview(self, workflow: str, values: dict[str, Any], reply: AgentReply) -> None:
        self.preview = (workflow, dict(values), reply, uuid4().hex)
        self.thread["selected_reply_id"] = self.preview[3]

    def _show_failure(self, error: ConversationError) -> None:
        if error.code in _SESSION_ERRORS:
            sync_history_owner(None)
            self.sync_owner(None)
            st.rerun()
        self.notice = (
            "The previous turn needs verification. Sending is paused for this chat."
            if error.code == "RECONCILIATION_REQUIRED"
            else "Could not update this chat. Please try again."
        )

    def queue_text(self, key: str, text: str) -> str | None:
        """Return rejected long text for the composer to restore without truncating."""
        handle = self.current_handle()
        if handle:
            try:
                self.chat.queue_text(handle, key, text)
            except ConversationError as error:
                if error.code == "INVALID_ARGUMENT" and len(text) > MAX_TEXT_CHARACTERS:
                    self.notice = (
                        "Use {limit:,} characters or fewer. Your text is still in the input box."
                    )
                    return text
                self._show_failure(error)
        return None

    def queue_guided(self, workflow, values, language):
        from app.accounts.conversation.guided import prepare_guided

        prepare_guided(workflow, values, language)
        handle = self.current_handle()
        if not handle:
            raise ConversationError("UNAUTHENTICATED")
        if self.chat.selected is None:
            if self.creation_needs_verification:
                raise ConversationError("STORAGE_OUTCOME_UNKNOWN")
            self.creation_needs_verification = True
            thread = self.backend.create_thread(handle, "New conversation")
            view = self.chat.open_thread(handle, thread.thread_id)
            if not self.current_handle():
                raise ConversationError("UNAUTHENTICATED")
            history = st.session_state.get("account_recent_chats")
            if history is None or history.owner != self.owner:
                raise ConversationError("IDENTITY_CHANGED")
            history.select(view)
            self.sync_view()
        event_key = self.chat.submission_key
        if event_key is None:
            raise ConversationError("REQUEST_IN_PROGRESS")
        return self.chat.queue_guided(handle, event_key, workflow, values, language)

    def can_reuse_empty(self, thread_id):
        thread = (
            self.thread if self.thread["id"] == thread_id else self.thread_drafts.get(thread_id, {})
        )
        recording = st.session_state.get("pending_speech", {})
        return not (
            self.chat.has_submission(thread_id)
            or any(
                thread.get(key)
                for key in ("speech_draft", "speech_error", "composer_draft", "workflow_preview")
            )
            or (thread_id == self.thread["id"] and self.preview is not None)
            or recording.get("thread_id") == thread_id
        )

    def chat_label(self, thread):
        """Keep approved first-exchange titles while respecting explicit account renames."""
        from app.ui.chat_titles import clean_request_text, conversation_title
        from app.ui.language import translate
        from app.ui.workflow_controls import display_workflow_message

        if thread.title not in {"New conversation", "新对话", "New Chat", "新建聊天"}:
            return thread.title, thread.title
        language = shared_language(st.session_state)
        view = self.chat.selected
        if (view is None or view.thread_id != thread.thread_id) and thread.preview_loaded:
            request = thread.first_request
            if not request:
                title = translate("New conversation", language)
                return title, title
            title = translate(
                conversation_title([{"role": "user", "content": request}], language=language),
                language,
            )
            return title, clean_request_text(request)
        if view is None or view.thread_id != thread.thread_id:
            view = self.title_snapshots.get(thread.thread_id)
            if view is None:
                handle = self.current_handle()
                if not handle:
                    raise ConversationError("UNAUTHENTICATED")
                view = self.backend.open_thread(handle, thread.thread_id)
                if not self.current_handle():
                    raise ConversationError("UNAUTHENTICATED")
                self.title_snapshots[thread.thread_id] = view
        messages = [
            {
                "role": message.role,
                "content": getattr(message, "text", getattr(message, "content", "")),
                "reply": message.reply,
            }
            for message in view.messages
        ]
        for index in range(0, len(messages) - 1, 2):
            reply = messages[index + 1]["reply"]
            request = reply.metadata.get("ui_workflow_request")
            if isinstance(request, dict):
                messages[index]["ui_workflow_request"] = request
        messages = _visible_messages(messages)
        title = translate(conversation_title(messages, language=language), language)
        first = next((i for i, m in enumerate(messages) if m["role"] == "user"), None)
        if first is None:
            return title, title
        following = messages[first + 1] if first + 1 < len(messages) else None
        hover = clean_request_text(display_workflow_message(messages[first], following, language))
        return title, hover

    def process_queued(self, *, show_spinner=True) -> bool:
        request = self.chat.submission
        if request is None or request.phase != "queued":
            return False
        handle = self.current_handle()
        if not handle:
            st.rerun()
        try:
            with (
                st.spinner(label("Sending and saving your message…"))
                if show_spinner
                else nullcontext()
            ):
                return self.chat.process_queued(handle, request.request_id) is not None
        except ConversationError as error:
            self._show_failure(error)
            return False

    def render_status(self) -> None:
        view, request = self.chat.selected, self.chat.submission
        pending = view.pending if view is not None else None
        if pending is not None:
            phase = "processing" if pending.status == "processing" else "reconciliation_required"
        else:
            phase = request.phase if request is not None else None
        message = {
            "processing": "A turn is processing. Refresh the chat to check its status.",
            "reconciliation_required": (
                "The previous turn needs verification. Sending is paused for this chat."
            ),
            "unknown": "The save outcome is unknown. Refresh the chat to check the stored history.",
            "reload_required": "The result was saved. Refresh the chat to load it.",
            "retryable": "Storage is unavailable. You can retry the same message.",
            "conflict": "The chat changed. Refresh it before sending again.",
        }.get(phase)
        if message:
            (st.info if phase == "processing" else st.warning)(label(message))
        elif self.notice:
            st.warning(label(self.notice).format(limit=MAX_TEXT_CHARACTERS))
        self.notice = None

        if (request is not None or pending is not None) and st.button(
            label("Refresh chat"), key=self.widget("refresh")
        ):
            self._recover()
        retry_key = self.chat.retry_key
        if (
            retry_key is not None
            and pending is None
            and st.button(label("Retry same message"), key=self.widget(f"retry_{retry_key}"))
        ):
            self._recover(retry_key)

    def _recover(self, retry_key: str | None = None) -> None:
        handle = self.current_handle()
        if not handle:
            st.rerun()
        request = self.chat.submission
        try:
            activity = "Sending and saving your message…" if retry_key else "Loading saved chat…"
            with st.spinner(label(activity)):
                if retry_key and request:
                    self.chat.retry(handle, request.request_id, retry_key)
                else:
                    self.chat.refresh(handle)
        except ConversationError as error:
            self._show_failure(error)
        st.rerun()


def _visible_messages(messages):
    # Keep an in-flight user message even though its assistant result is not stored yet.
    hidden = set()
    for index in range(0, len(messages) - 1, 2):
        reply = messages[index + 1].get("reply")
        if reply is not None and reply.metadata.get("ui_control") == "reset_request":
            hidden.update((index, index + 1))
    return [message for index, message in enumerate(messages) if index not in hidden]


def workspace_for(mode: AccountTestMode | RealAccountMode | None) -> AccountWorkspace | None:
    backend = mode.backend if mode is not None else None
    workspace = st.session_state.get(_STATE)
    if backend is None or (workspace is not None and workspace.mode != mode):
        if workspace is not None:
            workspace.sync_owner(None)
            sync_history_owner(None)
            st.session_state.pop("account_display_identity", None)
            st.session_state.pop(_STATE, None)
        workspace = None
    if mode is not None and backend is not None and workspace is None:
        workspace = AccountWorkspace(mode, backend)
        workspace.sync_owner(None)
        st.session_state[_STATE] = workspace
    return workspace


def current_workspace() -> AccountWorkspace | None:
    return st.session_state.get(_STATE)
