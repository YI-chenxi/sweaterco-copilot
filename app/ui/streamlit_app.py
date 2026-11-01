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
from datetime import date
from importlib import reload
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.contracts import AgentReply  # noqa: E402
from app.agent.orchestrator import AgentOrchestrator  # noqa: E402
from app.agent.service import AgentService  # noqa: E402
from app.agent.settings import AgentSettings, LLMSettings  # noqa: E402
from app.ui import evidence_follow as follow_presentation  # noqa: E402
from app.ui import language as language_catalog  # noqa: E402
from app.ui import localized_reply as reply_presentation  # noqa: E402
from app.ui import result_cards as card_presentation  # noqa: E402
from app.ui import workflow_controls as workflow_presentation  # noqa: E402
from app.ui.analysis_selection import ensure_reply_ids, selected_answer  # noqa: E402
from app.ui.chat_state import (  # noqa: E402
    clear_chat_request,
    ensure_chat_session,
    run_chat_turn,
    task_replies,
)
from app.ui.context_panels import contextual_sections  # noqa: E402

# Refresh only stateless UI presentation helpers. Agent sessions and cached
# services are retained when an existing process reloads the presentation code.
reload(language_catalog)
reload(reply_presentation)
reload(workflow_presentation)
reload(card_presentation)
reload(follow_presentation)
from app.ui.language import shared_language, translate  # noqa: E402
from app.ui.localized_reply import (  # noqa: E402
    display_origin,
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
    display_thread_title,
    display_workflow_message,
    select_workflow,
    toggle_workflow_panel,
    workflow_title,
)

st.set_page_config(
    page_title="SweaterCo | Operations Co-Pilot",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)
css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
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
    if "threads" not in state:
        state["threads"] = [{"id": 1, "title": "New conversation", "messages": []}]
    for thread in state["threads"]:
        ensure_chat_session(thread)
        ensure_reply_ids(thread)
    state.setdefault("active_thread", 1)
    state.setdefault("next_thread", 2)
    state.setdefault("workflow", None)
    state.setdefault("draft_text", "")
    state.setdefault("ui_language", shared_language(state))
    state.setdefault("analysis_open", True)
    state.setdefault("follow_evidence", True)
    state["follow_epoch"] = state.get("follow_epoch", 0) + 1
    # Launchpad is prominent for a new chat and becomes a compact, expandable
    # single row after the first successful assistant result.
    state.setdefault("workflow_panel_expanded", not bool(_thread()["messages"]))


def _thread() -> dict[str, Any]:
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

    language = shared_language(st.session_state)
    cache = st.session_state.setdefault("text_translations", {})
    texts = []
    for thread in st.session_state["threads"][:8]:
        texts.append(display_thread_title(thread, language))
        texts.append(thread["title"])
    messages = _thread()["messages"]
    for index, message in enumerate(messages):
        if message["role"] == "user":
            following = messages[index + 1] if index + 1 < len(messages) else None
            texts.append(display_workflow_message(message, following, language))
        else:
            reply = message["reply"]
            texts.append(reply_presentation.localized_result_text(reply, language) or reply.text)
            texts.extend(reply.warnings)
            texts.extend(localized_assumptions(reply.assumptions, language))
            texts.extend(item.detail for item in reply.evidence)
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
    n = st.session_state["next_thread"]
    st.session_state["next_thread"] = n + 1
    st.session_state["threads"].insert(0, {"id": n, "title": "New conversation", "messages": []})
    ensure_chat_session(st.session_state["threads"][0])
    st.session_state["active_thread"] = n
    st.session_state["workflow"] = None
    st.session_state["draft_text"] = ""
    st.session_state["workflow_panel_expanded"] = True


def _select_thread(thread_id: int) -> None:
    st.session_state["active_thread"] = thread_id
    st.session_state["workflow"] = None
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
    if thread["title"] == "New conversation":
        thread["title"] = text[:36].rstrip() + ("…" if len(text) > 36 else "")


def _append_reply(reply: AgentReply, origin: str) -> None:
    thread = _thread()
    thread["messages"].append({"role": "assistant", "reply": reply, "origin": origin})
    ensure_reply_ids(thread)
    thread["selected_reply_id"] = thread["messages"][-1]["ui_reply_id"]
    thread["scroll_to_answer"] = thread["selected_reply_id"]


def _view_evidence(reply_id: str) -> None:
    _thread()["selected_reply_id"] = reply_id
    st.session_state["analysis_open"] = True


def _follow_evidence_event(thread_id: int) -> None:
    from app.ui.evidence_follow import apply_follow_event

    event = st.session_state.get("follow_bridge_" + str(thread_id), {}).get("visible_answer")
    apply_follow_event(
        _thread(),
        event,
        enabled=st.session_state["follow_evidence"],
        panel_open=st.session_state["analysis_open"],
        active_thread=st.session_state["active_thread"],
    )


def _reset_follow_intent() -> None:
    st.session_state["follow_epoch"] = st.session_state.get("follow_epoch", 0) + 1


def _set_analysis_open(value: bool) -> None:
    st.session_state["analysis_open"] = value


def _run_guided(workflow: str, language: str, values: dict[str, Any]) -> None:
    display = workflow_title(workflow, values, language=language)
    decision = guided_decision(GUIDED_NAMES[workflow], language=language, **values)
    reply = guided_orchestrator().handle(decision)
    _append_user(display + "  ·  " + translate("Structured workflow", language))
    _thread()["messages"][-1]["ui_workflow_request"] = {
        "workflow": workflow,
        "values": dict(values),
    }
    _append_reply(reply, translate("Structured workflow · deterministic tools", language))
    if not reply.needs_clarification and reply.metadata.get("status") not in {
        "failed",
        "invalid",
        "validation_error",
        "empty",
    }:
        collapse_after_response(st.session_state)


def _send_text(prompt: str) -> None:
    """Run contextual chat and retain its final thread-local task state."""
    if not _chat_ready():
        st.session_state["chat_notice"] = (
            "AI Chat is not connected to an LLM yet. Configure a model key, "
            "or use Quick Workflows in the main workspace to run the same real business tools."
        )
        return
    _append_user(prompt)
    try:
        reply = run_chat_turn(_thread(), prompt, agent_service())
    except Exception:
        # Keep server credentials and tracebacks out of the UI.
        reply = AgentReply(
            text="The AI request failed. Please check the model connection and retry.",
            warnings=["Model call or tool execution failed; no business result was produced."],
            metadata={"status": "failed", "intent": "unknown"},
        )
    _append_reply(reply, "AI Chat · contextual tasks")
    st.session_state["clear_draft_on_rerun"] = True
    collapse_after_response(st.session_state)


def _nav_sidebar(chat_ready: bool) -> None:
    with st.sidebar:
        st.markdown(
            '<div class="sc-brand"><span class="sc-mark">SC</span>'
            "<div><strong>SweaterCo</strong><small>OPERATIONS CO-PILOT</small></div></div>",
            unsafe_allow_html=True,
        )
        st.selectbox(
            "Language / 语言",
            ["en", "zh"],
            key="ui_language",
            format_func=lambda code: "中文" if code == "zh" else "English",
        )
        st.button(
            "＋  " + _t("New conversation"),
            key="new_chat",
            use_container_width=True,
            type="primary",
            on_click=_new_thread,
        )
        navigation = (
            '<div class="sc-side-title">' + _t("MAIN NAVIGATION") + "</div>"
            '<div class="sc-nav-active">◈ &nbsp; ' + _t("AI Co-Pilot") + "</div>"
        )
        for icon, label in (
            ("▥", "Dashboards"),
            ("▤", "Data Hub"),
            ("▧", "Reports"),
            ("⚙", "Integrations"),
        ):
            navigation += (
                f'<div class="sc-nav-future">{icon} &nbsp; {_t(label)} '
                f"<span>{_t('Planned')}</span></div>"
            )
        st.markdown(navigation, unsafe_allow_html=True)
        st.markdown(
            '<div class="sc-side-rule"></div><div class="sc-side-title">'
            + _t("RECENT CHATS")
            + "</div>",
            unsafe_allow_html=True,
        )
        for thread in st.session_state["threads"][:8]:
            # Only the empty placeholder is localized; saved chat titles stay untouched.
            title = str(thread["title"])
            shown = _display_text(display_thread_title(thread, shared_language(st.session_state)))
            active = "● " if thread["id"] == st.session_state["active_thread"] else "◦ "
            st.button(
                active + shown,
                key=f"thread_{thread['id']}",
                use_container_width=True,
                on_click=_select_thread,
                args=(thread["id"],),
                help=_display_text(title),
            )
        st.caption(_t("Session chat history is not saved to GitHub."))
        st.markdown(
            '<div class="sc-side-rule"></div><div class="sc-side-title">'
            + _t("WORKSPACE")
            + "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="sc-workspace"><b>SweaterCo</b><small>'
            + _t("Factory snapshot: ")
            + _safe(SNAPSHOT.strftime("%d %b %Y"))
            + "</small><small>"
            + _t("Simulated CSV data")
            + "</small></div>",
            unsafe_allow_html=True,
        )
        status = "AI Chat configured" if chat_ready else "AI Chat needs model key"
        led = "sc-led online" if chat_ready else "sc-led"
        st.markdown(
            f'<div class="sc-service-state"><span class="{led}"></span> '
            + _t(status)
            + '<br><span class="sc-small">'
            + _t("Structured workflows available")
            + "</span></div>",
            unsafe_allow_html=True,
        )
        with st.expander(_t("AI Chat setup"), expanded=False):
            st.caption(_t("Configure the model locally, then restart the app."))


def _header() -> None:
    st.markdown(
        '<div class="sc-hero"><div class="sc-orb">✦</div>'
        '<div class="sc-hero-copy"><h1>'
        + _t("How can I help with your factory today?")
        + "</h1><p>"
        + _t("Ask naturally, or choose a workflow backed by factory data.")
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
            use_container_width=True,
            on_click=_toggle_workflow_panel,
        )
    cols = st.columns(4, gap="small")
    icons = ("▣", "▥", "☀", "✧")
    for col, label, icon in zip(cols, WORKFLOWS, icons, strict=True):
        with col:
            st.button(
                f"{icon}  {_t(label)}",
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

    @st.dialog(_t(flow), width="medium", on_dismiss=_dismiss_workflow)
    def parameters() -> None:
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
        '<div class="sc-label">'
        + _t("INTERPRETED REQUEST")
        + " <span>"
        + _t("Review explicit fields before running")
        + "</span></div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(f"**✧ {_t(flow)}** · {_t('Structured task helper')}")
        st.caption(
            _t(
                "This mode uses deterministic A1/A2 business tools; it does not "
                "claim to understand free text. Results appear in the same conversation."
            )
        )
        # Inputs commit on change, rather than remaining unsent inside st.form.
        # A's external switch can therefore rerun without dropping edited values.
        values: dict[str, Any] = {}
        if flow == "Order Status":
            values["order_id"] = st.text_input(
                _t("Order ID"),
                value=saved.get("order_id", "ORD-057"),
                placeholder=_t("Enter an order ID, e.g. ORD-057"),
                key=prefix + "_order_id",
            )
        elif flow == "Production Health":
            stages = ["KNITTING", "ASSEMBLY", "WASHING", "PACKING"]
            values["stage"] = st.selectbox(
                _t("Production stage"),
                stages,
                index=stages.index(saved.get("stage", "ASSEMBLY")),
                key=prefix + "_stage",
            )
        elif flow == "Morning Briefing":
            values["top_n"] = int(
                st.number_input(
                    _t("Priority findings"),
                    1,
                    20,
                    saved.get("top_n", 3),
                    step=1,
                    key=prefix + "_top_n",
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
                    key=prefix + "_category",
                )
            with cc[1]:
                values["pieces"] = int(
                    st.number_input(
                        _t("Quantity (pieces)"),
                        1,
                        100000,
                        saved.get("pieces", 800),
                        key=prefix + "_pieces",
                    )
                )
            with cc[2]:
                values["requested_delivery_date"] = st.date_input(
                    _t("Required delivery date"),
                    value=date.fromisoformat(saved.get("requested_delivery_date", "2026-04-25")),
                    min_value=date(2026, 4, 2),
                    key=prefix + "_date",
                ).isoformat()
        saved.update(values)
        if st.button(_t("Run analysis →"), key="run_workflow", type="primary"):
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
        if st.button(_t("Close helper"), key="close_structured", type="secondary"):
            st.session_state["workflow"] = None
            st.rerun()


def _toggle_analysis(identity: str) -> None:
    states = _thread().setdefault("expanded_answers", {})
    states[identity] = not states.get(identity, False)


def _reply_view(reply: AgentReply, identity: str) -> None:
    from app.ui.result_cards import card_summary

    _, _, tone, conclusion, metrics = card_summary(
        reply, shared_language(st.session_state), text=_reply_text(reply)
    )
    st.markdown(
        f'<div class="sc-assessment sc-assessment-{tone}">'
        f'<div class="sc-mini-label">{_t("Business Assessment")}</div>'
        f"<strong>{_safe(conclusion)}</strong></div>",
        unsafe_allow_html=True,
    )
    if metrics:
        st.markdown(
            '<div class="sc-mini-label">' + _t("KEY METRICS") + "</div>", unsafe_allow_html=True
        )
        for offset in range(0, len(metrics), 2):
            group = metrics[offset : offset + 2]
            for col, metric in zip(st.columns(len(group), gap="small"), group, strict=True):
                with col:
                    st.metric(metric.label, metric.value, help=metric.detail)
    st.markdown(
        '<div class="sc-mini-label">' + _t("Supporting Details") + "</div>", unsafe_allow_html=True
    )
    expanded = _thread().setdefault("expanded_answers", {}).get(identity, False)
    st.button(
        _t("Hide full analysis" if expanded else "Show full analysis"),
        key="full_" + identity,
        on_click=_toggle_analysis,
        args=(identity,),
        use_container_width=True,
    )
    if expanded:
        st.markdown(_reply_text(reply))


def _conversation() -> None:
    from app.ui.result_cards import card_summary

    chosen, _ = selected_answer(_thread())
    answer_number = 0
    messages = _thread()["messages"]
    for index, message in enumerate(messages):
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                following = messages[index + 1] if index + 1 < len(messages) else None
                st.write(
                    _display_text(
                        display_workflow_message(
                            message, following, shared_language(st.session_state)
                        )
                    )
                )
            else:
                answer_number += 1
                identity = message["ui_reply_id"]
                workflow, obj, _, _, _ = card_summary(
                    message["reply"],
                    shared_language(st.session_state),
                    text=_reply_text(message["reply"]),
                )
                marker = "sc-evidence-selected" if chosen is message else "sc-answer-reference"
                selection = " · " + _t("Selected") if chosen is message else ""
                st.markdown(
                    f'<span id="sc-answer-{_safe(identity)}" data-sc-answer="{_safe(identity)}" '
                    f'class="{marker}">'
                    f"{_t('Answer')} {answer_number:02d} · "
                    f"{_safe(_t(workflow))}{selection}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    display_origin(
                        message.get("origin", "Business analysis"),
                        shared_language(st.session_state),
                    )
                    + (" · " + obj if obj else "")
                )
                _reply_view(message["reply"], identity)
                copy, evidence = st.columns(2, gap="small")
                with copy:
                    with st.popover(_t("Copy"), use_container_width=True):
                        st.caption(
                            "点击复制图标复制完整分析。"
                            if shared_language(st.session_state) == "zh"
                            else "Use the copy icon to copy the full analysis."
                        )
                        st.code(_reply_text(message["reply"]), language=None, wrap_lines=True)
                with evidence:
                    st.button(
                        _t("View Evidence"),
                        key="evidence_" + identity,
                        on_click=_view_evidence,
                        args=(identity,),
                        use_container_width=True,
                        help=_t("Show only this answer's evidence, warnings and assumptions"),
                    )
                st.markdown(
                    f'<span id="sc-answer-end-{_safe(identity)}" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )


def _request_context() -> None:
    session = ensure_chat_session(_thread())
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
            clear_chat_request(_thread())
            st.rerun()


def _composer(chat_ready: bool) -> None:
    st.markdown(
        '<div class="sc-label">' + _t("ASK THE CO-PILOT") + "</div>", unsafe_allow_html=True
    )
    if st.session_state.pop("clear_draft_on_rerun", False):
        st.session_state["draft_text"] = ""
    with st.container(border=True):
        st.text_area(
            _t("One natural-language chat input"),
            key="draft_text",
            label_visibility="collapsed",
            placeholder=_t("Ask about orders, production, risks or feasibility..."),
            height=80,
        )
        hint, send = st.columns([3, 1], gap="small")
        with hint:
            st.caption("✦ " + _t("AI chat uses the model. Quick Workflows need no API key."))
        with send:
            clicked = st.button(
                _t("Send →"), key="send_message", type="primary", use_container_width=True
            )
        if clicked:
            prompt = st.session_state["draft_text"].strip()
            if not prompt:
                st.warning(_t("Enter a question first."))
            elif not chat_ready:
                st.warning(_t("Configure AI Chat or use Quick Workflows."))
            else:
                with st.spinner(_t("Agent is checking factory tools...")):
                    _send_text(prompt)
                st.rerun()
    notice = st.session_state.pop("chat_notice", None)
    if notice:
        st.warning(notice)


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
        st.markdown(
            '<div class="sc-details-title">' + _t("ANALYSIS DETAILS") + "</div>",
            unsafe_allow_html=True,
        )
        st.button(
            _t("Hide analysis panel"),
            key="hide_analysis",
            on_click=_set_analysis_open,
            args=(False,),
            use_container_width=True,
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

    if not st.session_state["analysis_open"]:
        st.rerun(scope="app")
    message, number = selected_answer(_thread())
    _details(
        message["reply"] if message else None, number, message["ui_reply_id"] if message else None
    )
    mount_follow(
        _thread(),
        st.session_state["follow_evidence"] and st.session_state["workflow"] not in WORKFLOWS,
        partial(_follow_evidence_event, _thread()["id"]),
    )


def _analysis_body(reply: AgentReply | None, reply_id: str | None) -> None:
    if reply is None:
        with st.container(border=True):
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
    with st.container(border=True):
        st.markdown(f"**◈ {_t('Supporting Evidence')}** · {len(rows)} {_t('records')}")
        if rows:
            sources = sorted({r["Source"] for r in rows})
            st.caption(_t("Sources: ") + ", ".join(sources[:5]))
            with st.expander(_t("View source rows"), expanded=False):
                st.dataframe(
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
                st.download_button(
                    _t("Export full evidence CSV"),
                    data=evidence_csv(reply).encode("utf-8-sig"),
                    mime="text/csv",
                    file_name="sweaterco_evidence.csv",
                    key=f"export_{_thread()['id']}_{reply_id}",
                )
        else:
            st.caption(_t("No evidence was provided for this answer."))

    if reply.warnings:
        with st.container(border=True):
            st.markdown(f"**⚠ {_t('Risk Warnings')}** · {len(reply.warnings)}")
            for w in reply.warnings:
                st.warning(_display_text(w))
    if reply.assumptions:
        with st.container(border=True):
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
                        st.dataframe(
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
    for section in contextual_sections(reply, language=shared_language(st.session_state)):
        with st.container(border=True):
            st.markdown(f"**{section['title']}**")
            if section.get("description"):
                st.caption(section["description"])
            if section.get("rows"):
                with st.expander(_t("View details"), expanded=bool(section.get("expanded", False))):
                    st.dataframe(
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


def main() -> None:
    _init_session()
    ready = _chat_ready()
    _prepare_history(ready)
    _nav_sidebar(ready)
    with st.sidebar:
        if any(value is None for value in st.session_state.get("text_translations", {}).values()):
            st.caption(
                "部分内容暂时无法翻译，请检查模型连接后重试。"
                if shared_language(st.session_state) == "zh"
                else "Some content could not be translated. Check the model connection and retry."
            )
            st.button(
                "重试翻译" if shared_language(st.session_state) == "zh" else "Retry translation",
                key="retry_translation",
                on_click=_retry_translations,
            )

    with st.container(key="workspace_layout"):
        columns = st.columns(
            [3.25, 1.55] if st.session_state["analysis_open"] else [1],
            gap="large",
            vertical_alignment="top",
        )
        center = columns[0]
        right = columns[1] if st.session_state["analysis_open"] else None
        with center, st.container(key="center_workspace"):
            if not st.session_state["analysis_open"]:
                st.button(
                    _t("Show analysis panel"),
                    key="show_analysis",
                    on_click=_set_analysis_open,
                    args=(True,),
                )
            _header()
            _workflow_launchpad()
            with _scroll_container("conversation_scroll", 400):
                _conversation()
                with st.container(key="composer_workspace"):
                    _request_context()
                    _composer(ready)
                    st.caption(
                        _t("DECISION SUPPORT ONLY") + " · " + _t("NOT LIVE FACTORY TELEMETRY")
                    )
        if right is not None:
            with right, st.container(key="analysis_workspace"):
                _analysis_fragment()

    from app.ui.answer_scroll import consume_scroll_target, scroll_script

    target = consume_scroll_target(_thread())
    if target:
        st.html(scroll_script(target), unsafe_allow_javascript=True)
    _workflow_dialog()


if __name__ == "__main__":
    main()
