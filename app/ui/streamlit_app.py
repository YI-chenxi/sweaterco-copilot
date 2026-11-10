"""SweaterCo Operations Co-Pilot — chat-first Streamlit web app.

Structured Quick Workflows run without an LLM. Free-text chat uses the enhanced
turn service and stores separate task state in each Streamlit conversation.
Business calculations remain in the existing deterministic A1/A2 tools.
"""

from __future__ import annotations

import html
import inspect
import logging
import sys
from contextlib import nullcontext
from datetime import date
from functools import partial
from importlib import reload
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.contracts import AgentReply  # noqa: E402
from app.agent.orchestrator import AgentOrchestrator  # noqa: E402
from app.agent.service import AgentService  # noqa: E402
from app.agent.settings import AgentSettings, LLMSettings  # noqa: E402
from app.agent.speech import SpeechError, SpeechService  # noqa: E402
from app.ui import evidence_follow as follow_presentation  # noqa: E402
from app.ui import language as language_catalog  # noqa: E402
from app.ui import localized_reply as reply_presentation  # noqa: E402
from app.ui import result_cards as card_presentation  # noqa: E402
from app.ui import themes as theme_presentation  # noqa: E402
from app.ui import workflow_controls as workflow_presentation  # noqa: E402
from app.ui.accounts.panels import auth_page, auth_retry  # noqa: E402
from app.ui.accounts.real_backend import RealAccountMode  # noqa: E402
from app.ui.accounts.recent_chats import (  # noqa: E402
    render_chat_management_dialog,
    render_recent_chats,
)
from app.ui.accounts.workspace import (  # noqa: E402
    AccountTestMode,
    current_workspace,
    workspace_for,
)
from app.ui.accounts.workspace import label as account_label  # noqa: E402
from app.ui.analysis_selection import ensure_reply_ids, selected_answer  # noqa: E402
from app.ui.chat_state import (  # noqa: E402
    clear_chat_request,
    ensure_chat_session,
    run_chat_turn,
    task_replies,
)
from app.ui.chat_titles import clean_request_text, conversation_title  # noqa: E402
from app.ui.context_panels import contextual_sections  # noqa: E402

# Refresh only stateless UI presentation helpers. Agent sessions and cached
# services are retained when an existing process reloads the presentation code.
reload(language_catalog)
reload(reply_presentation)
reload(workflow_presentation)
reload(card_presentation)
reload(theme_presentation)
reload(follow_presentation)
from app.ui.language import shared_language, translate  # noqa: E402
from app.ui.localized_reply import (  # noqa: E402
    localized_assumptions,
)
from app.ui.view_models import (  # noqa: E402
    evidence_csv,
    evidence_rows,
    guided_decision,
    metrics_for_reply,
)
from app.ui.workflow_controls import (  # noqa: E402
    collapse_after_response,
    display_workflow_message,
    select_workflow,
    toggle_workflow_panel,
    workflow_title,
)


def _configure_page() -> None:
    st.set_page_config(
        page_title="SweaterCo | Operations Co-Pilot",
        page_icon="🏭",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
    css += Path(__file__).with_name("proactive.css").read_text(encoding="utf-8")
    mode = theme_presentation.mode_name(st.session_state.get("ui_theme", "green"))
    css = theme_presentation.theme_styles(css, mode) + theme_presentation.native_styles(mode)
    css += Path(__file__).with_name("theme_controls.css").read_text(encoding="utf-8")
    css += (
        f".st-key-theme_{mode} button{{"
        "outline:1px solid var(--sc-teal)!important;outline-offset:3px}"
    )
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


WORKFLOWS = ("Order Status", "Production Health", "Morning Briefing", "Check Feasibility")
GUIDED_NAMES = {
    "Order Status": "Order status",
    "Production Health": "Production health",
    "Morning Briefing": "Morning briefing",
    "Check Feasibility": "Order feasibility",
}
SNAPSHOT = AgentSettings().reference_date


@st.cache_resource(show_spinner=False)
def agent_service() -> AgentService:
    return AgentService.from_env()


@st.cache_resource(show_spinner=False)
def guided_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator()


def _t(text: str) -> str:
    return translate(text, shared_language(st.session_state))


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _init_session() -> None:
    state = st.session_state
    from app.ui.proactive.state import init_state

    init_state(state)
    if current_workspace() is None:
        if "threads" not in state:
            state["threads"] = [{"id": 1, "title": "New conversation", "messages": []}]
        for thread in state["threads"]:
            _migrate_speech_draft(thread)
            ensure_chat_session(thread)
            ensure_reply_ids(thread)
            thread["title"] = conversation_title(thread["messages"])
        state.setdefault("active_thread", 1)
        state.setdefault("next_thread", 2)
    state.setdefault("workflow", None)
    state.setdefault("ui_language", shared_language(state))
    state.setdefault("ui_theme", "green")
    state.setdefault("analysis_open", True)
    state.setdefault("follow_evidence", True)
    state["follow_epoch"] = state.get("follow_epoch", 0) + 1
    # Launchpad is prominent for a new chat and becomes a compact, expandable
    # single row after the first successful assistant result.
    state.setdefault("workflow_panel_expanded", not bool(_thread()["messages"]))


def _thread() -> dict[str, Any]:
    workspace = current_workspace()
    if workspace is not None:
        return workspace.thread
    for thread in st.session_state["threads"]:
        if thread["id"] == st.session_state["active_thread"]:
            return thread
    return st.session_state["threads"][0]


@st.cache_resource(show_spinner=False)
def history_translator():
    from app.ui.history_translation import HistoryTranslator

    return HistoryTranslator()


def _display_text(text: str) -> str:
    from app.ui.history_translation import display

    return display(
        text,
        shared_language(st.session_state),
        st.session_state.setdefault("text_translations", {}),
    )


def _reply_text(reply: AgentReply) -> str:
    text = (
        reply_presentation.localized_result_text(reply, shared_language(st.session_state))
        or reply.text
    )
    return _display_text(text)


def _prepare_history(ready: bool) -> None:
    from app.ui.history_translation import cache_key, needs_translation, prepare

    workspace = current_workspace()
    if st.session_state.get("pending_chat") or (
        workspace is not None and workspace.chat.submission is not None
    ):
        return
    if workspace is not None and not _thread()["messages"]:
        return
    language = shared_language(st.session_state)
    cache = st.session_state.setdefault("text_translations", {})
    texts = []
    if current_workspace() is None:
        for thread in st.session_state["threads"]:
            texts.append(
                translate(conversation_title(thread["messages"], language=language), language)
            )
    messages = _thread()["messages"]
    for index, message in enumerate(messages):
        if message["role"] == "user":
            if current_workspace() is None and not message.get("ui_voice_request"):
                following = messages[index + 1] if index + 1 < len(messages) else None
                texts.append(
                    clean_request_text(display_workflow_message(message, following, language))
                )
            continue
        reply = message["reply"]
        texts.append(reply_presentation.localized_result_text(reply, language) or reply.text)
        texts.extend(reply.warnings)
        texts.extend(localized_assumptions(reply.assumptions, language))
        texts.extend(item.detail for item in reply.evidence[:15])
    pending = [
        t for t in texts if needs_translation(t, language) and cache_key(t, language) not in cache
    ]
    if not pending:
        return
    if ready:
        try:
            with st.spinner(
                "正在翻译历史内容…" if language == "zh" else "Translating conversation history…"
            ):
                prepare(pending, language, cache, history_translator())
        except Exception:
            for text in pending:
                cache[cache_key(text, language)] = None
    else:
        for text in pending:
            cache[cache_key(text, language)] = None


def _retry_translations() -> None:
    cache = st.session_state.get("text_translations", {})
    for key in list(cache):
        if cache[key] is None:
            del cache[key]


def _new_thread() -> None:
    from app.ui.chat_state import reusable_empty_thread

    st.session_state["ui_page"] = "copilot"
    recording_thread = st.session_state.get("pending_speech", {}).get("thread_id")
    candidates = [t for t in st.session_state["threads"] if t["id"] != recording_thread]
    empty = reusable_empty_thread(candidates, st.session_state["active_thread"])
    if empty is None:
        n = st.session_state["next_thread"]
        st.session_state["next_thread"] = n + 1
        empty = {"id": n, "title": "New conversation", "messages": []}
        st.session_state["threads"].insert(0, empty)
    n = empty["id"]
    ensure_chat_session(empty)
    st.session_state["active_thread"] = n
    st.session_state["workflow"] = None
    st.session_state["chat_prompt"] = ""
    st.session_state["workflow_panel_expanded"] = True


def _select_thread(thread_id: int) -> None:
    st.session_state["ui_page"] = "copilot"
    st.session_state["active_thread"] = thread_id
    st.session_state["workflow"] = None
    st.session_state["chat_prompt"] = ""
    st.session_state["workflow_panel_expanded"] = not bool(_thread()["messages"])


def _select_workflow(name: str) -> None:
    select_workflow(st.session_state, name)


def _toggle_workflow_panel() -> None:
    toggle_workflow_panel(st.session_state)


def _chat_ready() -> bool:
    try:
        s = LLMSettings.from_env()
        return s.provider == "openai" and bool(s.api_key)
    except ValueError:
        return False


def _append_user(text: str) -> None:
    thread = _thread()
    thread["messages"].append({"role": "user", "content": text})
    thread["title"] = conversation_title(thread["messages"])


def _append_reply(reply: AgentReply, origin: str) -> None:
    thread = _thread()
    thread["messages"].append({"role": "assistant", "reply": reply, "origin": origin})
    ensure_reply_ids(thread)
    thread["selected_reply_id"] = thread["messages"][-1]["ui_reply_id"]
    thread["scroll_to_answer"] = thread["selected_reply_id"]
    thread["title"] = conversation_title(thread["messages"])


def _account_widget(key: str) -> str:
    workspace = current_workspace()
    return workspace.widget(key) if workspace is not None else key


def _evidence_widget(identity: str) -> str:
    key = "evidence_" + identity
    return "evidence_" + _account_widget(key) if current_workspace() is not None else key


def _view_evidence(reply_id: str) -> None:
    workspace = current_workspace()
    if workspace is not None and not workspace.current_handle():
        return
    _thread()["selected_reply_id"] = reply_id
    st.session_state["analysis_open"] = True


def _follow_evidence_event(thread_id: int | str) -> None:
    from app.ui.evidence_follow import apply_follow_event

    workspace = current_workspace()
    if workspace is not None and not workspace.current_handle():
        return
    event = st.session_state.get("follow_bridge_" + str(thread_id), {}).get("visible_answer")
    apply_follow_event(
        _thread(),
        event,
        enabled=st.session_state["follow_evidence"],
        panel_open=st.session_state["analysis_open"],
        active_thread=_thread()["id"]
        if workspace is not None
        else st.session_state["active_thread"],
    )


def _reset_follow_intent() -> None:
    st.session_state["follow_epoch"] = st.session_state.get("follow_epoch", 0) + 1


def _set_analysis_open(value: bool) -> None:
    st.session_state["analysis_open"] = value


def _run_guided(workflow: str, language: str, values: dict[str, Any]) -> None:
    workspace = current_workspace()
    if workspace is not None:
        workspace.queue_guided(workflow, values, language)
        collapse_after_response(st.session_state)
        return
    display = workflow_title(workflow, values, language=language)
    decision = guided_decision(GUIDED_NAMES[workflow], language=language, **values)
    reply = guided_orchestrator().handle(decision)
    _append_user(display)
    _thread()["messages"][-1]["ui_workflow_request"] = {
        "workflow": workflow,
        "values": dict(values),
    }
    _append_reply(reply, "Quick Workflows")
    if not reply.needs_clarification and reply.metadata.get("status") not in {
        "failed",
        "invalid",
        "validation_error",
        "empty",
    }:
        collapse_after_response(st.session_state)


def _queue_chat_prompt(thread_id: int | str) -> None:
    """Queue text or bind a recording to the thread that rendered the composer."""
    value = st.session_state.get("chat_prompt")
    if value is None:
        return
    if isinstance(value, str):
        if _thread()["id"] == thread_id:
            _queue_text(value)
        return
    prompt = value.text or ""
    audio = getattr(value, "audio", None)
    if audio is None:
        if _thread()["id"] == thread_id:
            _queue_text(prompt)
        return

    _queue_recording(value, thread_id)


def _queue_recording(value, thread_id: int | str) -> None:
    audio = getattr(value, "audio", None)
    if audio is None:
        return
    thread = _speech_thread(thread_id)
    if (
        thread is None
        or st.session_state.get("pending_speech")
        or audio.file_id in st.session_state.get("handled_recordings", ())
    ):
        return
    thread["composer_token"] = uuid4().hex
    thread.pop("composer_sequence", None)
    st.session_state["pending_speech"] = {
        "id": audio.file_id,
        "thread_id": thread_id,
        "audio": audio.getvalue(),
        "typed_text": value.text or "",
    }


def _queue_text(prompt: str) -> bool:
    if not prompt.strip() or st.session_state.get("pending_chat"):
        return False
    workspace = current_workspace()
    if workspace is not None:
        event_key = workspace.chat.submission_key
        if event_key is None:
            return False
        rejected = workspace.queue_text(event_key, prompt)
        accepted = rejected is None and workspace.chat.submission is not None
        if accepted:
            _clear_composer(_thread())
        return accepted
    if not _chat_ready():
        st.session_state["chat_notice"] = _t(
            "AI Chat needs a model key. Check AI Configure or use Quick Workflows."
        )
        return False
    _append_user(prompt)
    if _thread().get("speech_draft"):
        _thread()["messages"][-1]["ui_voice_request"] = True
    _clear_composer(_thread())
    st.session_state["pending_chat"] = {"thread_id": _thread()["id"], "prompt": prompt}
    return True


_SPEECH_ERRORS = {
    "empty_audio": "The recording is empty. Please record again.",
    "invalid_audio": "The recording could not be read. Please record again.",
    "too_large": "Recording exceeds 5 MiB. Please make it shorter.",
    "too_long": "Recording exceeds 60 seconds. Please make it shorter.",
    "no_speech": "No clear speech was found. Please try again.",
    "timeout": "Transcription timed out. Please try again.",
    "unavailable": "Transcription is unavailable. You can still type.",
}


def _speech_thread(thread_id: int | str) -> dict[str, Any] | None:
    workspace = current_workspace()
    if workspace is not None:
        if not workspace.current_handle():
            return None
        if workspace.thread["id"] == thread_id:
            return workspace.thread
        return workspace.thread_drafts.get(thread_id)
    return next(
        (item for item in st.session_state["threads"] if item["id"] == thread_id),
        None,
    )


def _migrate_speech_draft(thread: dict[str, Any]) -> None:
    """Preserve drafts from a retained session before rendering new widgets."""
    if "speech_draft" not in thread or "composer_draft" in thread:
        return
    draft = thread["speech_draft"]
    if isinstance(draft, dict):
        text = draft.get("text")
        draft_id = draft.get("id")
        if isinstance(draft_id, str) and draft_id and isinstance(text, str):
            edited = st.session_state.get(
                _account_widget(f"speech_draft_{thread['id']}_{draft_id}")
            )
            if isinstance(edited, str):
                text = edited
    elif isinstance(draft, str):
        # The old widget held the last edits separately from the transcript.
        edited = st.session_state.pop(f"speech_draft_{thread['id']}", None)
        text = edited if isinstance(edited, str) else draft
    else:
        text = None

    if isinstance(text, str) and text.strip():
        thread["speech_draft"] = {
            "id": str(draft.get("id", uuid4().hex))
            if isinstance(draft, dict)
            else f"legacy-{uuid4().hex}",
            "text": text,
        }
        thread["composer_draft"] = text
        thread["composer_prefill"] = True
    else:
        thread.pop("speech_draft")


def _process_pending_speech() -> None:
    pending = st.session_state.get("pending_speech")
    if not pending:
        return
    submission_id = pending["id"]
    handled = st.session_state.setdefault("handled_recordings", set())
    thread = _speech_thread(pending["thread_id"])
    if submission_id in handled or thread is None:
        st.session_state.pop("pending_speech", None)
        return

    with st.spinner(_t("Transcribing recording..."), show_time=True):
        st.session_state.pop("pending_speech", None)
        handled.add(submission_id)
        _transcribe_speech_draft(thread, pending)


def _transcribe_speech_draft(thread: dict[str, Any], pending: dict[str, Any]) -> None:
    typed = pending["typed_text"]
    thread.pop("speech_rerecord", None)
    try:
        transcript = SpeechService.from_env().transcribe(pending["audio"])
    except SpeechError as exc:
        thread["speech_error"] = exc.code
        transcript = ""
    except Exception:
        thread["speech_error"] = "unavailable"
        transcript = ""
    else:
        thread.pop("speech_error", None)

    # The recording carries the entire current input, including manual edits/deletions.
    text = "\n".join(part for part in (typed, transcript) if part)
    thread["composer_draft"] = text
    thread["composer_prefill"] = True
    thread["composer_token"] = uuid4().hex
    thread.pop("composer_sequence", None)
    if text.strip():
        thread["speech_draft"] = {
            "id": pending["id"],
            "text": text,
        }
    else:
        thread.pop("speech_draft", None)


def _clear_composer(thread):
    for key in ("speech_draft", "speech_rerecord", "speech_error", "composer_prefill"):
        thread.pop(key, None)
    thread["composer_draft"] = ""
    thread["composer_token"] = uuid4().hex
    thread.pop("composer_sequence", None)


def _process_pending_chat() -> None:
    """Run only after the conversation, including the new user bubble, renders."""
    pending = st.session_state.pop("pending_chat", None)
    if not pending:
        return
    thread = next(
        (item for item in st.session_state["threads"] if item["id"] == pending["thread_id"]),
        None,
    )
    if thread is None:
        return
    indicator = st.empty()
    with indicator.container(), st.chat_message("assistant"):
        st.markdown(
            '<div class="sc-thinking" role="status" aria-live="polite">'
            '<span class="sc-thinking-ring" aria-hidden="true"></span>'
            "<span>" + _t("I’m thinking…") + "</span></div>",
            unsafe_allow_html=True,
        )
    try:
        reply = run_chat_turn(thread, pending["prompt"], agent_service())
    except Exception:
        # Keep server credentials and tracebacks out of the UI.
        reply = AgentReply(
            text="The AI request failed. Please check the model connection and retry.",
            warnings=["Model call or tool execution failed; no business result was produced."],
            metadata={"status": "failed", "intent": "unknown"},
        )
    finally:
        indicator.empty()
    thread["messages"].append(
        {"role": "assistant", "reply": reply, "origin": "AI Chat · contextual tasks"}
    )
    ensure_reply_ids(thread)
    thread["selected_reply_id"] = thread["messages"][-1]["ui_reply_id"]
    thread["scroll_to_answer"] = thread["selected_reply_id"]
    thread["title"] = conversation_title(thread["messages"])
    _capture_proactive_reply(reply)
    collapse_after_response(st.session_state)
    st.rerun()


def _capture_proactive_reply(reply: AgentReply) -> None:
    if isinstance(reply.metadata.get("proactive"), dict):
        from app.ui.proactive.journey import capture_proposal, refresh_pending
        from app.ui.proactive.state import bind_scope

        application = _proactive_application()
        bind_scope(st.session_state, application)
        capture_proposal(reply.metadata["proactive"], thread_id=_thread()["id"])
        refresh_pending(application)


def _set_theme(mode: str) -> None:
    st.session_state["ui_theme"] = theme_presentation.mode_name(mode)


def _select_page(page: str) -> None:
    st.session_state.pop("active_composer", None)
    st.session_state["ui_page"] = page
    st.session_state["workflow"] = None
    st.session_state.get("proactive_ui", {}).pop("return_target", None)
    st.session_state.get("proactive_ui", {}).pop("preparing_request", None)
    st.session_state.get("proactive_ui", {}).pop("pending_decision", None)


def _theme_controls(width: int | Literal["stretch"] = "stretch") -> None:
    with st.container(key="theme_controls", horizontal=True, gap=None, width=width):
        for theme in theme_presentation.MODES:
            label = _t(theme_presentation.MODE_LABELS[theme])
            st.button(
                label,
                key="theme_" + theme,
                help=label,
                on_click=_set_theme,
                args=(theme,),
                width=24,
            )


def _language_control(width: int | Literal["stretch"] = "stretch") -> None:
    st.selectbox(
        "Language / 语言",
        ["en", "zh"],
        key="ui_language",
        format_func=lambda code: "中文" if code == "zh" else "English",
        label_visibility="collapsed",
        width=width,
    )


def _auth_preferences() -> None:
    _theme_controls(width=90)
    _language_control(width=112)


def _nav_sidebar(chat_ready: bool) -> None:
    workspace = current_workspace()
    sidebar_owner = workspace.owner if workspace is not None else None
    with st.sidebar, st.container(key="sidebar_layout"):
        st.markdown(
            '<div class="sc-brand"><span class="sc-mark">SC</span>'
            "<div><strong>SweaterCo</strong><small>OPERATIONS CO-PILOT</small></div></div>",
            unsafe_allow_html=True,
        )
        if workspace is None:
            st.button(
                "＋  " + _t("New conversation"),
                key="new_chat",
                use_container_width=True,
                type="primary",
                on_click=_new_thread,
            )
        else:
            with st.container(key="new_chat"):
                account_new_chat = st.empty()
        from app.ui.proactive.locale import tr as proactive_tr

        st.markdown(
            '<div class="sc-side-title">' + _t("MAIN NAVIGATION") + "</div>", unsafe_allow_html=True
        )
        with st.container(key="main_navigation_items"):
            for page, icon, label in (
                ("copilot", "forum", "AI Co-Pilot"),
                ("briefings", "wb_sunny", "Morning Briefing"),
                ("watches", "notifications", "Watches & Alerts"),
                ("actions", "fact_check", "Actions"),
            ):
                active = st.session_state["ui_page"] == page
                count = st.session_state.get("proactive_ui", {}).get("pending_count")
                shown_label = proactive_tr(label, st.session_state)
                if page == "actions" and isinstance(count, int) and count > 0:
                    shown_label += " · " + str(count)
                st.button(
                    shown_label,
                    icon=f":material/{icon}:",
                    key="pa_nav_" + page,
                    on_click=_select_page,
                    args=(page,),
                    type="primary" if active else "secondary",
                    use_container_width=True,
                )
        if workspace is not None:
            private_chats = st.empty()
            with private_chats.container():
                selected = render_recent_chats(
                    workspace.backend,
                    workspace.mode.session,
                    expected_owner=workspace.owner,
                    open_thread=workspace.chat.open_thread,
                    on_session_error=lambda: workspace.sync_owner(None),
                    compact=True,
                    new_chat_panel=account_new_chat.container,
                    chat_label=workspace.chat_label,
                    can_reuse=workspace.can_reuse_empty,
                    reuse_current=lambda: (
                        workspace.chat.selected.thread_id
                        if (
                            workspace.chat.selected is not None
                            and not workspace.chat.selected.messages
                            and workspace.chat.selected.pending is None
                            and workspace.chat.submission is None
                            and not workspace.thread.get("speech_draft")
                            and workspace.preview is None
                            and not st.session_state.get("pending_speech")
                        )
                        else None
                    ),
                )
            if workspace.owner is not None and not workspace.current_handle():
                private_chats.empty()
                st.warning(account_label("Your session ended. Sign in again."))
            if selected is None:
                workspace.chat.deselect()
            if workspace.owner is None:
                private_chats.empty()
                return
        else:
            st.markdown(
                '<div class="sc-side-rule sc-recent-rule"></div><div class="sc-side-title">'
                + _t("RECENT CHATS")
                + "</div>",
                unsafe_allow_html=True,
            )
            language = shared_language(st.session_state)
            with st.container(height=300, border=False, key="recent_chats"):
                for thread in st.session_state["threads"]:
                    title = conversation_title(thread["messages"], language=language)
                    active = "● " if thread["id"] == st.session_state["active_thread"] else "◦ "
                    messages = thread["messages"]
                    first = next((i for i, m in enumerate(messages) if m["role"] == "user"), None)
                    if first is None:
                        hover = _t("New conversation")
                    else:
                        following = messages[first + 1] if first + 1 < len(messages) else None
                        hover = display_workflow_message(messages[first], following, language)
                    st.button(
                        active + _display_text(_t(title)),
                        key=f"thread_{thread['id']}",
                        use_container_width=True,
                        on_click=_select_thread,
                        args=(thread["id"],),
                        help=_display_text(hover),
                    )
        with st.container(key="sidebar_footer"):
            st.markdown('<div class="sc-side-rule"></div>', unsafe_allow_html=True)
            with st.container(key="workspace_heading"):
                st.markdown(
                    '<div class="sc-side-title">' + _t("WORKSPACE") + "</div>",
                    unsafe_allow_html=True,
                )
                _theme_controls()
            state_class = "online" if chat_ready else "offline"
            state_label = _t("Configured" if chat_ready else "Not configured")
            st.markdown(
                '<div class="sc-ai-config" role="status" aria-label="AI Configure: '
                + _safe(state_label)
                + '"><span class="sc-ai-config-label">'
                + _t("AI Configure")
                + '</span><span class="sc-ai-config-state '
                + state_class
                + '">'
                + '<span class="sc-led '
                + state_class
                + '" aria-hidden="true"></span>'
                + _safe(state_label)
                + "</span></div>",
                unsafe_allow_html=True,
            )
            _language_control()
            if workspace is not None:
                footer_fallback = st.empty()
                workspace.render_auth(
                    signed_in=partial(
                        st.container,
                        key="account_menu",
                        horizontal=True,
                        vertical_alignment="center",
                        gap="small",
                    ),
                    signed_out=footer_fallback.container,
                    show_notice=False,
                )
                if workspace.owner != sidebar_owner:
                    private_chats.empty()
                    footer_fallback.empty()
                    workspace.sync_owner(None)
                    return
            if any(v is None for v in st.session_state.get("text_translations", {}).values()):
                st.button(
                    _t("Retry translation"),
                    key="retry_translation",
                    on_click=_retry_translations,
                    help=_t(
                        "Some content could not be translated. "
                        "Check the model connection and retry."
                    ),
                )


def _header() -> None:
    st.markdown(
        '<div class="sc-hero"><div class="sc-orb">✦</div>'
        '<div class="sc-hero-copy"><h1>'
        + _t("How can I help with your factory today?")
        + "</h1><p>"
        + _t("Your factory, at a glance.")
        + '</p></div><div class="sc-snapshot-card" aria-label="'
        + _t("Simulated factory data date")
        + '"><div class="sc-snapshot-heading">'
        + _t("FACTORY DATA SNAPSHOT")
        + '</div><div class="sc-snapshot-date">'
        + _safe(SNAPSHOT.strftime("%d %b %Y").upper())
        + "</div>"
        '<div class="sc-snapshot-disclaimer">' + _t("SIMULATED · NOT LIVE") + "</div></div></div>",
        unsafe_allow_html=True,
    )


def _workflow_launchpad() -> None:
    """Four real deterministic workflows, all in the conversation workspace.

    After an answer, collapse to a compact one-line launcher. This remains
    available for reopening, without duplicating navigation in the sidebar.
    """
    expanded = st.session_state["workflow_panel_expanded"]
    if not expanded:
        st.button(
            "✧  " + _t("QUICK WORKFLOWS") + " · " + _t("4 business tasks") + " ▾ " + _t("Expand"),
            key="expand_workflows",
            use_container_width=True,
            on_click=_toggle_workflow_panel,
            help=_t("Open the four structured factory workflows"),
        )
        return
    title_col, toggle_col = st.columns([5, 1], gap="small", vertical_alignment="center")
    with title_col:
        st.markdown(
            '<div class="sc-label sc-workflow-title">'
            + _t("QUICK WORKFLOWS")
            + " <span>"
            + _t("Choose a task to open its parameter dialog")
            + "</span></div>",
            unsafe_allow_html=True,
        )
    with toggle_col:
        st.button(
            "▴ " + _t("Collapse"),
            key="collapse_workflows",
            on_click=_toggle_workflow_panel,
        )
    cols = st.columns(4, gap="small")
    icons = ("▣", "▥", "☀", "✧")
    for col, label, icon in zip(cols, WORKFLOWS, icons, strict=True):
        with col:
            st.button(
                f"{icon}  {_t('Instant Briefing' if label == 'Morning Briefing' else label)}",
                key="center_" + label.replace(" ", "_"),
                type="primary" if st.session_state["workflow"] == label else "secondary",
                use_container_width=True,
                on_click=_select_workflow,
                args=(label,),
                help=_t("Open workflow parameters in a dialog"),
            )


def _dismiss_workflow() -> None:
    st.session_state["workflow"] = None


def _workflow_dialog() -> None:
    flow = st.session_state["workflow"]
    if flow not in WORKFLOWS:
        return

    from app.ui.dialog_layout import mount_dialog_layout

    # Keep the invisible geometry bridge out of the dialog's form layout.
    mount_dialog_layout(shared_language(st.session_state))

    workspace = current_workspace()
    scope = workspace.scope if workspace is not None else None

    @st.dialog(_t(flow), width="medium", on_dismiss=_dismiss_workflow)
    def parameters() -> None:
        if workspace is not None and (workspace.scope != scope or not workspace.current_handle()):
            st.rerun(scope="app")
        _structured_helper()

    parameters()


def _structured_helper() -> None:
    flow = st.session_state["workflow"]
    if flow not in WORKFLOWS:
        return
    language = shared_language(st.session_state)
    # Stable canonical identifiers, independent of translated labels.
    saved = _thread().setdefault("workflow_inputs", {}).setdefault(flow, {})
    prefix = f"wf_{_thread()['id']}_{flow.replace(' ', '_')}"
    st.markdown(
        '<div class="sc-label"><span class="sc-resizable-dialog-marker"></span>'
        + _t("INTERPRETED REQUEST")
        + " <span>"
        + _t("Review explicit fields before running")
        + "</span></div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(f"**✧ {_t(flow)}**")
        st.caption(_t("Review the details, then run the analysis."))
        # Inputs commit on change, rather than remaining unsent inside st.form.
        # A's external switch can therefore rerun without dropping edited values.
        values: dict[str, Any] = {}
        if flow == "Order Status":
            values["order_id"] = st.text_input(
                _t("Order ID"),
                value=saved.get("order_id", "ORD-057"),
                placeholder=_t("Enter an order ID, e.g. ORD-057"),
                key=_account_widget(prefix + "_order_id"),
            )
        elif flow == "Production Health":
            stages = ["KNITTING", "ASSEMBLY", "WASHING", "PACKING"]
            values["stage"] = st.selectbox(
                _t("Production stage"),
                stages,
                index=stages.index(saved.get("stage", "ASSEMBLY")),
                key=_account_widget(prefix + "_stage"),
            )
        elif flow == "Morning Briefing":
            values["top_n"] = int(
                st.number_input(
                    _t("Priority findings"),
                    1,
                    20,
                    saved.get("top_n", 3),
                    step=1,
                    key=_account_widget(prefix + "_top_n"),
                )
            )
        elif flow == "Check Feasibility":
            cc = st.columns([1, 1, 1.3], gap="small")
            with cc[0]:
                categories = ["TOPS", "ACCESSORIES"]
                values["category"] = st.selectbox(
                    _t("Category"),
                    categories,
                    index=categories.index(saved.get("category", "TOPS")),
                    key=_account_widget(prefix + "_category"),
                )
            with cc[1]:
                values["pieces"] = int(
                    st.number_input(
                        _t("Quantity (pieces)"),
                        1,
                        100000,
                        saved.get("pieces", 800),
                        key=_account_widget(prefix + "_pieces"),
                    )
                )
            with cc[2]:
                values["requested_delivery_date"] = st.date_input(
                    _t("Required delivery date"),
                    value=date.fromisoformat(saved.get("requested_delivery_date", "2026-04-25")),
                    min_value=date(2026, 4, 2),
                    key=_account_widget(prefix + "_date"),
                ).isoformat()
        saved.update(values)
        with st.container(key="run_workflow") if current_workspace() is not None else nullcontext():
            run = st.button(
                _t("Run analysis →"), key=_account_widget("run_workflow"), type="primary"
            )
        if run:
            succeeded = False
            try:
                if flow == "Order Status" and not values["order_id"].strip():
                    st.error(_t("Order ID is required."))
                else:
                    execution_values = dict(values)
                    if "order_id" in execution_values:
                        execution_values["order_id"] = execution_values["order_id"].strip().upper()
                    with st.spinner(_t("Checking factory data and business rules...")):
                        _run_guided(flow, language, execution_values)
                    succeeded = True
            except (ValueError, TypeError):
                logging.exception("Invalid request fields for workflow %s", flow)
                st.error(_t("Invalid fields. Check the input parameters."))
            except Exception:
                logging.exception("Guided workflow failed: %s", flow)
                st.error(
                    _t(
                        "Unable to run the workflow. Please retry or "
                        "contact your project maintainer."
                    )
                )
            if succeeded:
                st.rerun()


def _toggle_analysis(identity: str) -> None:
    workspace = current_workspace()
    if workspace is not None and not workspace.current_handle():
        return
    states = _thread().setdefault("expanded_answers", {})
    states[identity] = not states.get(identity, False)


def _reply_view(reply: AgentReply, identity: str) -> None:
    if isinstance(reply.metadata.get("proactive"), dict):
        from app.ui.proactive.pages import render_chat_result

        render_chat_result(reply.metadata["proactive"], identity)
        return
    from app.ui.result_cards import card_summary

    _, _, tone, conclusion, metrics = card_summary(
        reply, shared_language(st.session_state), text=_reply_text(reply)
    )
    with st.container(key="answer_summary_" + identity):
        st.markdown(
            f'<div class="sc-assessment sc-assessment-{tone}">'
            f"<strong>{_safe(conclusion)}</strong></div>",
            unsafe_allow_html=True,
        )
        st.button(
            _t("View Evidence"),
            icon=":material/manage_search:",
            key=_evidence_widget(identity),
            on_click=_view_evidence,
            args=(identity,),
            help=_t("Show only this answer's evidence, warnings and assumptions"),
        )
    if metrics:
        st.markdown(
            '<div class="sc-mini-label">' + _t("KEY METRICS") + "</div>", unsafe_allow_html=True
        )
        for offset in range(0, len(metrics), 3):
            group = metrics[offset : offset + 3]
            for col, metric in zip(st.columns(len(group), gap="small"), group, strict=True):
                with col:
                    st.metric(metric.label, metric.value, help=metric.detail)
    st.markdown(
        '<div class="sc-mini-label">' + _t("Supporting Details") + "</div>", unsafe_allow_html=True
    )
    expanded = _thread().setdefault("expanded_answers", {}).get(identity, False)
    st.button(
        _t("Hide full analysis" if expanded else "Show full analysis"),
        key=_account_widget("full_" + identity),
        on_click=_toggle_analysis,
        args=(identity,),
        use_container_width=True,
    )
    if expanded:
        from app.ui.copy_button import copy_analysis

        with st.container(key="full_analysis_" + identity):
            with st.container(key="analysis_copy_" + identity):
                copy_analysis(_reply_text(reply), shared_language(st.session_state), identity)
            st.markdown(_reply_text(reply))


def _conversation() -> None:
    chosen, _ = selected_answer(_thread())
    account_mode = current_workspace() is not None
    messages = _thread()["messages"]
    for index, message in enumerate(messages):
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                if (account_mode and not message.get("ui_workflow_request")) or message.get(
                    "ui_voice_request"
                ):
                    _user_message(message["content"])
                    continue
                following = messages[index + 1] if index + 1 < len(messages) else None
                content = message["content"]
                label = display_workflow_message(
                    message, following, shared_language(st.session_state)
                )
                if message.get("ui_workflow_request") or label != content:
                    st.write(label)
                else:
                    st.write(_display_text(clean_request_text(label)))
            else:
                identity = message["ui_reply_id"]
                marker = "sc-evidence-selected" if chosen is message else "sc-answer-reference"
                st.markdown(
                    f'<span id="sc-answer-{_safe(identity)}" data-sc-answer="{_safe(identity)}" '
                    f'class="{marker}" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )
                _reply_view(message["reply"], identity)
                st.markdown(
                    f'<span id="sc-answer-end-{_safe(identity)}" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )


def _user_message(text: str) -> None:
    # Native Markdown belongs to the host palette; isolated HTML did not inherit it.
    st.markdown(
        '<p style="white-space: pre-wrap; overflow-wrap: anywhere;">' + _safe(text) + "</p>",
        unsafe_allow_html=True,
    )


def _request_context() -> None:
    account_mode = current_workspace() is not None
    session = _thread().get("agent_session") if account_mode else ensure_chat_session(_thread())
    if session is None:
        return
    pending = [task for task in session.state.tasks if task.status.value == "pending"]
    if not pending and not session.global_issues:
        return
    with st.expander(_t("Requests waiting for details"), expanded=True):
        if session.global_issues:
            st.info(_t("Please start fresh and submit up to two requests."))
        for task in pending:
            slots = task.decision.slots
            intent = task.decision.intent.value
            labels = {
                "category": "product category",
                "order_id": "order number",
                "pieces": "order quantity",
                "requested_delivery_date": "delivery date",
                "stage": "production stage",
                "top_n": "number of briefing items",
            }
            missing = ", ".join(
                _t(labels.get(field, field.replace("_", " ")))
                for field in task.decision.missing_slots
            )
            if intent == "order_status":
                label = (
                    f"{_t('Order')} {slots.get('order_id')}"
                    if slots.get("order_id")
                    else _t("Order check")
                )
            elif intent == "production_normality":
                label = (
                    f"{slots.get('stage')} · {_t('Production check')}"
                    if slots.get("stage")
                    else _t("Production check")
                )
            elif intent == "order_feasibility":
                details = " ".join(
                    value
                    for value in (
                        f"{slots.get('pieces')}-piece" if slots.get("pieces") else "",
                        str(slots.get("category")) if slots.get("category") else "",
                    )
                    if value
                )
                label = (
                    f"{details} · {_t('Order assessment')}"
                    if details
                    else _t("New-order assessment")
                )
            elif intent == "morning_briefing":
                label = _t("Morning briefing")
            else:
                label = _t("Request")
            st.caption(
                label
                + (
                    f" · {_t('Still needed')}: {missing}"
                    if missing
                    else " · " + _t("Needs clarification")
                )
            )
        if st.button(_t("Start a fresh request"), key="reset_chat_request"):
            if account_mode:
                current_workspace().queue_guided(
                    "Start a fresh request", {}, shared_language(st.session_state)
                )
            else:
                clear_chat_request(_thread())
            st.rerun()


def _queue_account_prompt(widget_key: str, event_key: str | None) -> None:
    workspace = current_workspace()
    text = st.session_state.get(widget_key)
    if workspace is not None and event_key is not None and workspace.current_handle():
        if event_key != workspace.chat.submission_key:
            return
        if not isinstance(text, str):
            if getattr(text, "audio", None) is not None:
                _queue_recording(text, workspace.thread["id"])
                return
            text = getattr(text, "text", "") or ""
        rejected = workspace.queue_text(event_key, text)
        if rejected is not None:
            st.session_state[widget_key] = rejected
        elif workspace.chat.submission is not None:
            _clear_composer(workspace.thread)


def _composer() -> None:
    """Render the native input as the fixed center surface's inline footer.

    The sibling scroll container owns long conversations, so this input stays
    at the bottom of the surface in new, empty and populated conversations.
    """
    thread = _thread()
    _migrate_speech_draft(thread)
    if thread.get("speech_error"):
        st.warning(_t(_SPEECH_ERRORS.get(thread["speech_error"], _SPEECH_ERRORS["unavailable"])))
    workspace = current_workspace()
    if workspace is not None:
        event_key = workspace.chat.submission_key
        widget_key = workspace.widget(f"prompt_{event_key or 'disabled'}")
        _prime_composer(thread, widget_key)
        st.chat_input(
            _t("Ask about orders, production, risks or feasibility..."),
            key=widget_key,
            on_submit=_queue_account_prompt,
            args=(widget_key, event_key),
            disabled=event_key is None,
            accept_audio=True,
        )
        return
    _prime_composer(thread, "chat_prompt")
    st.chat_input(
        _t("Ask about orders, production, risks or feasibility..."),
        key="chat_prompt",
        on_submit=_queue_chat_prompt,
        args=(thread["id"],),
        accept_audio=True,
    )
    notice = st.session_state.pop("chat_notice", None)
    if notice:
        st.warning(notice)


def _prime_composer(thread, widget_key):
    thread.setdefault("composer_token", uuid4().hex)
    identity = (thread["id"], widget_key)
    if (
        thread.pop("composer_prefill", False)
        or st.session_state.get("active_composer") != identity
        or widget_key not in st.session_state
    ):
        st.session_state[widget_key] = thread.get("composer_draft", "")
    st.session_state["active_composer"] = identity
    thread["composer_widget_key"] = widget_key


@st.fragment
def _composer_draft_bridge():
    from app.ui.composer_draft import apply_draft_event, mount_draft_bridge

    workspace = current_workspace()
    thread = _thread()
    if not thread.get("composer_widget_key"):
        return
    owner, scope = (workspace.owner, workspace.scope) if workspace is not None else (None, None)
    key = _account_widget(f"composer_bridge_{thread['id']}")

    def save():
        if workspace is not None and (workspace.owner != owner or workspace.scope != scope):
            return
        if _thread()["id"] == thread["id"]:
            apply_draft_event(thread, st.session_state.get(key, {}).get("draft"))

    mount_draft_bridge(thread, thread["composer_widget_key"], key, save)


def _scroll_container(key: str, height: int):
    """Native bounded scrolling, without forcing the user to the newest answer."""
    options = (
        {"autoscroll": False} if "autoscroll" in inspect.signature(st.container).parameters else {}
    )
    return st.container(height=height, border=False, key=key, **options)


def _details(
    reply: AgentReply | None, answer_number: int | None = None, reply_id: str | None = None
) -> None:
    with st.container(key="analysis_header"):
        with st.container(key="analysis_heading"):
            st.markdown(
                '<div class="sc-details-title">' + _t("ANALYSIS DETAILS") + "</div>",
                unsafe_allow_html=True,
            )
            st.button(
                _t("Hide"),
                icon=":material/chevron_right:",
                key="hide_analysis",
                help=_t("Hide analysis panel"),
                on_click=_set_analysis_open,
                args=(False,),
            )
        from app.ui.evidence_follow import follow_supported

        st.toggle(
            _t("Follow answers while scrolling"),
            disabled=not follow_supported(),
            key="follow_evidence",
            on_change=_reset_follow_intent,
            help=_t("View Evidence selects an answer. Following resumes on your next chat scroll."),
        )
        if reply is not None:
            intent = str(reply.metadata.get("intent", "unknown"))
            intent_name = {
                "order_status": "Order status",
                "production_normality": "Production check",
                "morning_briefing": "Morning briefing",
                "order_feasibility": "Order assessment",
                "unknown": "General question",
            }.get(intent, "Analysis")
            context = reply.metadata.get("structured_result")
            identifier = (
                (context.get("order_id") or context.get("stage"))
                if isinstance(context, dict)
                else None
            )
            st.markdown(
                '<div class="sc-analysis-context">'
                + _t("Selected answer")
                + " · "
                + _t("Answer")
                + f" {answer_number} · "
                + _safe(_t(intent_name))
                + (" · " + _safe(identifier) if identifier else "")
                + "</div>",
                unsafe_allow_html=True,
            )
    with _scroll_container(f"analysis_scroll_{_thread()['id']}_{reply_id or 'empty'}", 500):
        _analysis_body(reply, reply_id)


@st.fragment
def _analysis_fragment() -> None:
    """Scroll events refresh only the analysis rail, not the conversation or translations."""
    from functools import partial

    from app.ui.evidence_follow import mount_follow

    workspace = current_workspace()
    if not st.session_state["analysis_open"] or (
        workspace is not None and not workspace.current_handle()
    ):
        st.rerun(scope="app")
    message, number = selected_answer(_thread())
    preview = workspace.preview if workspace is not None else None
    if preview is not None and _thread().get("selected_reply_id") == preview[3]:
        _details(preview[2], (number or 0) + 1, preview[3])
        return
    _details(
        message["reply"] if message else None, number, message["ui_reply_id"] if message else None
    )
    if _thread()["id"] is not None:
        mount_follow(
            _thread(),
            st.session_state["follow_evidence"] and st.session_state["workflow"] not in WORKFLOWS,
            partial(_follow_evidence_event, _thread()["id"]),
        )


def _themed_dataframe(data, **options) -> None:
    st.dataframe(
        theme_presentation.table_for_mode(data, st.session_state.get("ui_theme", "green")),
        **options,
    )


def _analysis_body(reply: AgentReply | None, reply_id: str | None, *, key_prefix: str = "") -> None:
    if reply is None:
        with st.container(border=True, key=key_prefix + "detail_empty"):
            st.markdown("**" + _t("Evidence & context") + "**")
            st.caption(
                _t(
                    "Once you run a real analysis, source records, warnings, "
                    "assumptions and relevant business details will appear here."
                )
            )
        return
    intent = str(reply.metadata.get("intent", "unknown"))
    rows = evidence_rows(reply)
    with st.container(border=True, key=key_prefix + "detail_evidence"):
        st.markdown(f"**◈ {_t('Supporting Evidence')}** · {len(rows)} {_t('records')}")
        if rows:
            sources = sorted({r["Source"] for r in rows})
            st.caption(_t("Sources: ") + ", ".join(sources[:5]))
            with st.expander(_t("View source rows"), expanded=False):
                _themed_dataframe(
                    [
                        {_t(k): _display_text(v) if k == "Detail" else v for k, v in row.items()}
                        for row in rows[:15]
                    ],
                    hide_index=True,
                    use_container_width=True,
                    height=40 + 36 * min(15, len(rows)),
                )
                if len(rows) > 15:
                    st.caption(
                        f"显示 {len(rows)} 条来源记录中的前 15 条。"
                        if shared_language(st.session_state) == "zh"
                        else f"Showing 15 of {len(rows)} source references."
                    )
                workspace = current_workspace()
                if workspace is None or workspace.current_handle():
                    st.download_button(
                        _t("Export full evidence CSV"),
                        data=evidence_csv(reply).encode("utf-8-sig"),
                        mime="text/csv",
                        file_name="sweaterco_evidence.csv",
                        key=_account_widget(f"{key_prefix}export_{_thread()['id']}_{reply_id}"),
                    )
        else:
            st.caption(_t("No evidence was provided for this answer."))

    if reply.warnings:
        with st.container(border=True, key=key_prefix + "detail_warnings"):
            st.markdown(f"**⚠ {_t('Risk Warnings')}** · {len(reply.warnings)}")
            for w in reply.warnings:
                st.warning(_display_text(w))
    if reply.assumptions:
        with st.container(border=True, key=key_prefix + "detail_assumptions"):
            st.markdown(f"**◇ {_t('Key Assumptions')}** · {len(reply.assumptions)}")
            shown_assumptions = localized_assumptions(
                reply.assumptions, shared_language(st.session_state)
            )
            for text in shown_assumptions:
                st.markdown("• " + _safe(_display_text(text)))

    parts = task_replies(reply)
    if parts and not isinstance(reply.metadata.get("structured_result"), dict):
        for index, (_, source_reply) in enumerate(parts, 1):
            task_name = source_reply.metadata.get("intent", "Result").replace("_", " ").title()
            with st.expander(f"{_t('Request')} {index} · {_t(task_name)}"):
                for metric in metrics_for_reply(
                    source_reply, language=shared_language(st.session_state)
                ):
                    st.metric(metric.label, metric.value, help=metric.detail)
                for section in contextual_sections(
                    source_reply, language=shared_language(st.session_state)
                ):
                    st.markdown(f"**{section['title']}**")
                    if section.get("description"):
                        st.caption(section["description"])
                    if section.get("rows"):
                        _themed_dataframe(
                            section["rows"],
                            hide_index=True,
                            use_container_width=True,
                            height=40 + 36 * len(section["rows"]),
                        )
                    for label, value in section.get("facts", []):
                        st.write(f"{label}: {value}")

    # Optional contextual modules are strictly derived from existing A2 results.
    # In particular, the current CSV snapshot does NOT support inventory or
    # shipping-document cards from the concept image.
    for index, section in enumerate(
        contextual_sections(reply, language=shared_language(st.session_state))
    ):
        with st.container(border=True, key=f"{key_prefix}detail_context_{index}"):
            st.markdown(f"**{section['title']}**")
            if section.get("description"):
                st.caption(section["description"])
            if section.get("rows"):
                with st.expander(_t("View details"), expanded=bool(section.get("expanded", False))):
                    _themed_dataframe(
                        section["rows"],
                        hide_index=True,
                        use_container_width=True,
                        height=40 + 36 * len(section["rows"]),
                    )
            for label, value in section.get("facts", []):
                aa, bb = st.columns([1.2, 1])
                with aa:
                    st.caption(label)
                with bb:
                    st.write(value)

    with st.expander(_t("Technical trace"), expanded=False):
        st.json(
            {
                "intent": intent,
                "status": reply.metadata.get("status", "not specified"),
                "rule_version": reply.metadata.get("rule_version"),
                "language": reply.metadata.get("language"),
                "source_records": len(reply.evidence),
            }
        )


def _proactive_details(details) -> None:
    from app.ui.proactive.pages import render_context, t

    with st.container(key="analysis_header"):
        with st.container(key="analysis_heading"):
            st.markdown(
                '<div class="sc-details-title">' + _t("ANALYSIS DETAILS") + "</div>",
                unsafe_allow_html=True,
            )
            st.button(
                _t("Hide"),
                icon=":material/chevron_right:",
                key="hide_analysis",
                on_click=_set_analysis_open,
                args=(False,),
            )
        st.caption(t("Selected item"))
    with _scroll_container("analysis_scroll_proactive", 500):
        render_context(details, _analysis_body)


def _proactive_application():
    """Never use the anonymous demo identity for signed-in proactive pages."""
    workspace = current_workspace()
    if workspace is None:
        try:
            return getattr(agent_service(), "proactive_application", None)
        except Exception:
            return None
    if not workspace.current_handle():
        return None
    if workspace.proactive_application is None:
        from app.agent.proactive.integration import build_proactive_application

        def principal():
            return workspace.owner if workspace.current_handle() else None

        def identity_field(name):
            owner = principal()
            return getattr(owner, name, None)

        workspace.proactive_application = build_proactive_application(
            llm_client=agent_service().parser.client,
            actor_provider=lambda: identity_field("user_id"),
            workspace_provider=lambda: identity_field("workspace_id"),
            authorizer=lambda actor, scope, operation, proposal: (
                (owner := principal()) is not None
                and (actor, scope) == (owner.user_id, owner.workspace_id)
            ),
        )
    return workspace.proactive_application


def main(
    account_test_mode: AccountTestMode | None = None,
    *,
    real_account_mode: RealAccountMode | None = None,
) -> None:
    _configure_page()
    mode = real_account_mode if real_account_mode is not None else account_test_mode
    workspace = workspace_for(mode)
    if mode is not None:
        notice = (
            account_label("Offline account test mode. No real login or cloud storage.")
            if isinstance(mode, AccountTestMode)
            else None
        )
        signed_out = partial(auth_page, _auth_preferences, notice=notice)
        if workspace is None:
            with signed_out():
                st.error(
                    account_label("Account service is not configured. Contact the app maintainer.")
                )
            return
        if not workspace.validate_owner():
            workspace.render_auth(signed_in=st.container, signed_out=signed_out)
            return

    _init_session()
    ready = _chat_ready() if workspace is None else getattr(mode, "chat_ready", False)
    page = st.session_state["ui_page"]
    if workspace is None and page == "copilot":
        _prepare_history(ready)
    _nav_sidebar(ready)
    if workspace is not None:
        if workspace.owner is None:
            with auth_page(lambda: None, notice=notice):
                st.warning(account_label("Your session ended. Sign in again."))
                auth_retry()
            return
        workspace.sync_view()
        if page == "copilot":
            _prepare_history(ready)
    if not st.session_state["analysis_open"]:
        st.button(
            _t("Show analysis panel"),
            icon=":material/keyboard_double_arrow_left:",
            key="show_analysis",
            help=_t("Show analysis panel"),
            on_click=_set_analysis_open,
            args=(True,),
        )
    with st.container(key="workspace"):
        columns = st.columns(
            [3.25, 1.55] if st.session_state["analysis_open"] else [1],
            gap="medium",
            vertical_alignment="top",
        )
        with columns[0], st.container(key="conversation_surface"):
            with _scroll_container("conversation_scroll", 500):
                if page == "copilot":
                    _header()
                    # Normal document flow until this launcher reaches the top.
                    with st.container(key="workflow_dock"):
                        _workflow_launchpad()
                    if workspace is not None:
                        request = workspace.chat.submission
                        waiting = st.empty()
                        if request is not None and request.phase == "queued":
                            with waiting.container():
                                _conversation()
                                if (
                                    not request.guided
                                    or request.guided["workflow"] != "Start a fresh request"
                                ):
                                    with st.chat_message("user"):
                                        _user_message(request.text)
                                with st.chat_message("assistant"):
                                    st.markdown(
                                        '<div class="sc-thinking" role="status" aria-live="polite">'
                                        '<span class="sc-thinking-ring" aria-hidden="true"></span>'
                                        "<span>" + _t("I’m thinking…") + "</span></div>",
                                        unsafe_allow_html=True,
                                    )
                        processed = workspace.process_queued(show_spinner=False)
                        waiting.empty()
                        if processed:
                            workspace.sync_view()
                            if workspace.thread["messages"]:
                                answer = workspace.thread["messages"][-1]
                                workspace.thread["selected_reply_id"] = answer["ui_reply_id"]
                                workspace.thread["scroll_to_answer"] = answer["ui_reply_id"]
                                reply = answer.get("reply")
                                if reply is not None:
                                    _capture_proactive_reply(reply)
                            collapse_after_response(st.session_state)
                            st.rerun()
                        workspace.sync_view()
                        workspace.render_status()
                    _conversation()
                    if workspace is None:
                        _process_pending_chat()
                    _request_context()
                else:
                    from app.ui.proactive.pages import render_page

                    application = _proactive_application()
                    details = render_page(page, application, guided_orchestrator())
            if page == "copilot":
                _process_pending_speech()
                _composer()
        if st.session_state["analysis_open"]:
            with columns[1], st.container(key="analysis_workspace"):
                if page == "copilot":
                    _analysis_fragment()
                else:
                    _proactive_details(details)
    from app.ui.answer_scroll import consume_scroll_target, scroll_script

    target = consume_scroll_target(_thread()) if page == "copilot" else None
    if target:
        st.html(scroll_script(target), unsafe_allow_javascript=True)
    if page == "copilot":
        from app.ui.workflow_dock import mount_workflow_dock

        mount_workflow_dock(_thread()["id"], st.session_state["workflow_panel_expanded"])
    _workflow_dialog()
    if page == "copilot":
        with st.container(key="composer_bridge_slot"):
            _composer_draft_bridge()
    chat_dialog_open = False
    if workspace is not None and not st.session_state["workflow"]:
        chat_dialog_open = render_chat_management_dialog(
            workspace.backend,
            workspace.mode.session,
            workspace.owner,
            chat_label=workspace.chat_label,
            on_session_error=lambda: workspace.sync_owner(None),
        )
    from app.ui.proactive.navigation import mount_navigation

    mount_navigation()
    if (
        (
            st.session_state.get("proactive_ui", {}).get("review_dialog")
            or st.session_state.get("proactive_ui", {}).get("input_error")
        )
        and not st.session_state["workflow"]
        and not chat_dialog_open
    ):
        from app.ui.proactive.journey import render_review_dialog

        application = _proactive_application()
        render_review_dialog(application)


if __name__ == "__main__":
    main()
