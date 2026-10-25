"""SweaterCo Operations Co-Pilot — chat-first Streamlit web app.

UI-only integration. A1/A2 tools, AgentService, AgentOrchestrator, and the
AgentReply contract are untouched. Structured Quick Workflows run without an
LLM; free-text conversation requires the existing model configuration.
"""
from __future__ import annotations

import html
import logging
import sys
from datetime import date, datetime
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
from app.ui.view_models import evidence_csv, evidence_rows, findings_for_reply, guided_decision, metrics_for_reply  # noqa: E402
from app.ui.context_panels import contextual_sections, reply_headline  # noqa: E402
from app.ui.workflow_controls import (  # noqa: E402
    workflow_title, select_workflow, toggle_workflow_panel, collapse_after_response,
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


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _init_session() -> None:
    state = st.session_state
    if "threads" not in state:
        state["threads"] = [{"id": 1, "title": "New conversation", "messages": []}]
    state.setdefault("active_thread", 1)
    state.setdefault("next_thread", 2)
    state.setdefault("workflow", None)
    state.setdefault("draft_text", "")
    state.setdefault("reply_language", "English")
    # Launchpad is prominent for a new chat and becomes a compact, expandable
    # single row after the first successful assistant result.
    state.setdefault("workflow_panel_expanded", not bool(_thread()["messages"]))


def _thread() -> dict[str, Any]:
    for thread in st.session_state["threads"]:
        if thread["id"] == st.session_state["active_thread"]:
            return thread
    return st.session_state["threads"][0]


def _new_thread() -> None:
    n = st.session_state["next_thread"]
    st.session_state["next_thread"] = n + 1
    st.session_state["threads"].insert(0, {"id": n, "title": "New conversation", "messages": []})
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
    _thread()["messages"].append({"role": "assistant", "reply": reply, "origin": origin})


def _last_reply() -> AgentReply | None:
    for message in reversed(_thread()["messages"]):
        if message["role"] == "assistant" and isinstance(message.get("reply"), AgentReply):
            return message["reply"]
    return None


def _run_guided(workflow: str, language: str, values: dict[str, Any]) -> None:
    display = workflow_title(workflow, values)
    decision = guided_decision(GUIDED_NAMES[workflow], language=language, **values)
    reply = guided_orchestrator().handle(decision)
    _append_user(display + "  ·  Structured workflow")
    _append_reply(reply, "Structured workflow · deterministic tools")
    collapse_after_response(st.session_state)


def _send_text(prompt: str) -> None:
    """Free-text uses the existing B agent; never fake LLM results."""
    if not _chat_ready():
        st.session_state["chat_notice"] = (
            "AI Chat is not connected to an LLM yet. Configure a model key, "
            "or use Quick Workflows in the main workspace to run the same real business tools."
        )
        return
    _append_user(prompt)
    try:
        reply = agent_service().handle_text(prompt)
    except Exception:
        # Keep server credentials and tracebacks out of the UI.
        reply = AgentReply(
            text="The AI request failed. Please check the model connection and retry.",
            warnings=["Model call or tool execution failed; no business result was produced."],
            metadata={"status": "failed", "intent": "unknown"},
        )
    _append_reply(reply, "AI Chat · model routed")
    st.session_state["clear_draft_on_rerun"] = True
    collapse_after_response(st.session_state)


def _nav_sidebar(chat_ready: bool) -> None:
    with st.sidebar:
        st.markdown(
            '<div class="sc-brand"><span class="sc-mark">SC</span>'
            '<div><strong>SweaterCo</strong><small>OPERATIONS CO-PILOT</small></div></div>',
            unsafe_allow_html=True,
        )
        st.button("＋  New conversation", key="new_chat", use_container_width=True, type="primary", on_click=_new_thread)

        # Sidebar now contains main navigation and recent chats only.
        # All four Quick Workflows live in the central workspace.
        st.markdown(
            '<div class="sc-side-title">MAIN NAVIGATION</div>'
            '<div class="sc-nav-active">◈ &nbsp; AI Co-Pilot</div>'
            '<div class="sc-nav-future">▥ &nbsp; Dashboards <span>Planned</span></div>'
            '<div class="sc-nav-future">▤ &nbsp; Data Hub <span>Planned</span></div>'
            '<div class="sc-nav-future">▧ &nbsp; Reports <span>Planned</span></div>'
            '<div class="sc-nav-future">⚙ &nbsp; Integrations <span>Planned</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="sc-side-rule"></div><div class="sc-side-title">RECENT CHATS</div>', unsafe_allow_html=True)
        threads = st.session_state["threads"]
        for thread in threads[:8]:
            title = str(thread["title"])
            active = "● " if thread["id"] == st.session_state["active_thread"] else "◦ "
            st.button(
                active + title,
                key=f"thread_{thread['id']}",
                use_container_width=True,
                on_click=_select_thread,
                args=(thread["id"],),
                help=title,
            )
        st.caption("Conversations stay in this browser session; not saved to GitHub.")

        st.markdown('<div class="sc-side-rule"></div><div class="sc-side-title">WORKSPACE</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="sc-workspace"><b>SweaterCo</b><small>Factory snapshot: '
            + _safe(SNAPSHOT.strftime("%d %b %Y"))
            + "</small><small>Simulated CSV data</small></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="sc-service-state">'
            + ('<span class="sc-led online"></span> AI Chat configured' if chat_ready else '<span class="sc-led"></span> AI Chat needs model key')
            + '<br><span class="sc-small">Structured workflows available</span></div>',
            unsafe_allow_html=True,
        )
        with st.expander("AI Chat setup", expanded=False):
            st.caption("Copy .env.example to .env at the project root. Set MODEL_PROVIDER=openai, a working OPENAI_MODEL and OPENAI_API_KEY. Restart the app. Never commit secrets.")


def _header() -> None:
    st.markdown(
        '<div class="sc-hero"><div class="sc-orb">✦</div>'
        '<div class="sc-hero-copy"><h1>How can I help with your factory today?</h1>'
        '<p>Ask naturally, or launch a guided workflow. Clear answers, backed by factory data.</p>'
        '</div><div class="sc-snapshot-card" aria-label="Simulated factory data date">'
        '<div class="sc-snapshot-heading">FACTORY DATA SNAPSHOT</div>'
        '<div class="sc-snapshot-date">' + _safe(SNAPSHOT.strftime("%d %b %Y").upper()) + '</div>'
        '<div class="sc-snapshot-disclaimer">SIMULATED · NOT LIVE</div>'
        '</div></div>',
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
            "✧  Quick Workflows · 4 business tasks    ▾  Expand",
            key="expand_workflows",
            use_container_width=True,
            on_click=_toggle_workflow_panel,
            help="Open the four structured factory workflows",
        )
        return
    title_col, toggle_col = st.columns([5, 1], gap="small", vertical_alignment="center")
    with title_col:
        st.markdown('<div class="sc-label sc-workflow-title">QUICK WORKFLOWS <span>Choose a task to fill its parameters below</span></div>', unsafe_allow_html=True)
    with toggle_col:
        st.button("▴ Collapse", key="collapse_workflows", use_container_width=True, on_click=_toggle_workflow_panel)
    cols = st.columns(4, gap="small")
    icons = ("▣", "▥", "☀", "✧")
    for col, label, icon in zip(cols, WORKFLOWS, icons, strict=True):
        with col:
            st.button(
                f"{icon}  {label}",
                key="center_" + label.replace(" ", "_"),
                type="primary" if st.session_state["workflow"] == label else "secondary",
                use_container_width=True,
                on_click=_select_workflow,
                args=(label,),
                help="Open the structured request form below",
            )


def _structured_helper() -> None:
    flow = st.session_state["workflow"]
    if flow not in WORKFLOWS:
        return
    st.markdown('<div class="sc-label">INTERPRETED REQUEST <span>Review explicit fields before running</span></div>', unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(f"**✧ {flow}**  ·  Structured task helper")
        st.caption("This mode uses deterministic A1/A2 business tools; it does not claim to understand free text. Results appear in the same conversation.")
        with st.form(key="workflow_form", clear_on_submit=False):
            lang_choice = st.selectbox("Response language", ["English", "中文"], key="reply_language")
            values: dict[str, Any] = {}
            if flow == "Order Status":
                values["order_id"] = st.text_input("Order ID", value="ORD-057").strip().upper()
            elif flow == "Production Health":
                values["stage"] = st.selectbox("Production stage", ["KNITTING", "ASSEMBLY", "WASHING", "PACKING"], index=1)
            elif flow == "Morning Briefing":
                values["top_n"] = int(st.number_input("Priority findings", 1, 20, 3, step=1))
            elif flow == "Check Feasibility":
                cc = st.columns([1, 1, 1.3], gap="small")
                with cc[0]:
                    values["category"] = st.selectbox("Category", ["TOPS", "ACCESSORIES"])
                with cc[1]:
                    values["pieces"] = int(st.number_input("Quantity (pieces)", 1, 100000, 800))
                with cc[2]:
                    values["requested_delivery_date"] = st.date_input(
                        "Required delivery date", value=date(2026, 4, 25), min_value=date(2026, 4, 2)
                    ).isoformat()
            b1, b2 = st.columns([3, 1], gap="small")
            with b2:
                submitted = st.form_submit_button("Run analysis →", type="primary", use_container_width=True)
        if submitted:
            succeeded = False
            try:
                with st.spinner("Checking factory data and business rules..."):
                    _run_guided(flow, "zh" if lang_choice == "中文" else "en", values)
                succeeded = True
            except (ValueError, TypeError):
                logging.exception("Invalid request fields for workflow %s", flow)
                st.error("Invalid fields. Check the input parameters.")
            except Exception:
                logging.exception("Guided workflow failed: %s", flow)
                st.error("Unable to run the workflow. See the PowerShell traceback for details.")
            if succeeded:
                # Keep Streamlit's rerun control flow outside try/except.
                st.rerun()
        if st.button("Close helper", key="close_structured", type="secondary"):
            st.session_state["workflow"] = None
            st.rerun()


def _reply_view(reply: AgentReply) -> None:
    """Show only backend-grounded values. No invented progress percentages."""
    heading, tone = reply_headline(reply)
    with st.container(border=True):
        st.markdown(
            f'<div class="sc-answer-banner {tone}"><small>BUSINESS ASSESSMENT</small>'
            f'<strong>{_safe(heading)}</strong></div>',
            unsafe_allow_html=True,
        )
        st.write(reply.text)
        metrics = [m for m in metrics_for_reply(reply) if m.value != "—"]
        if metrics:
            st.markdown('<div class="sc-mini-label">KEY METRICS</div>', unsafe_allow_html=True)
            for offset in range(0, len(metrics), 3):
                group = metrics[offset:offset + 3]
                for col, met in zip(st.columns(len(group), gap="small"), group, strict=True):
                    with col:
                        st.metric(met.label, met.value, help=met.detail)
        findings = findings_for_reply(reply)
        if findings:
            with st.expander(f"Priority findings · {len(findings)}", expanded=True):
                st.dataframe(findings, hide_index=True, use_container_width=True)
        if reply.needs_clarification:
            st.info(reply.suggested_question or "Please clarify the request.")
            st.caption("Current B-agent requests are handled independently; a follow-up does not automatically fill earlier missing parameters.")
        elif reply.metadata.get("intent") == "order_feasibility":
            st.markdown('<div class="sc-next-step">Decision support only · This is an estimate, not a delivery guarantee. Review assumptions before committing.</div>', unsafe_allow_html=True)
        elif reply.metadata.get("status") not in {"failed", "empty"}:
            st.markdown('<div class="sc-next-step">Review evidence before acting on this assessment.</div>', unsafe_allow_html=True)


def _conversation() -> None:
    # Messages appear naturally; no "Conversation" title or empty-state filler.
    for message in _thread()["messages"]:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            else:
                st.caption(message.get("origin", "Business analysis"))
                _reply_view(message["reply"])


def _composer(chat_ready: bool) -> None:
    st.markdown('<div class="sc-label">ASK THE CO-PILOT</div>', unsafe_allow_html=True)
    if st.session_state.pop("clear_draft_on_rerun", False):
        st.session_state["draft_text"] = ""
    with st.container(border=True):
        st.text_area(
            "One natural-language chat input",
            key="draft_text",
            label_visibility="collapsed",
            placeholder="Ask about orders, production, risks or feasibility...",
            height=80,
        )
        hint, send = st.columns([3, 1], gap="small")
        with hint:
            st.caption("✦ Natural-language AI uses the model. Quick Workflows run without an API key.")
        with send:
            clicked = st.button("Send →", key="send_message", type="primary", use_container_width=True)
        if clicked:
            prompt = st.session_state["draft_text"].strip()
            if not prompt:
                st.warning("Enter a question first.")
            elif not chat_ready:
                st.warning("Natural-language mode requires a valid model key. You can still use Quick Workflows above without an API key.")
            else:
                with st.spinner("Agent is checking factory tools..."):
                    _send_text(prompt)
                st.rerun()
    notice = st.session_state.pop("chat_notice", None)
    if notice:
        st.warning(notice)


def _details(reply: AgentReply | None) -> None:
    st.markdown('<div class="sc-details-title">ANALYSIS DETAILS</div>', unsafe_allow_html=True)
    if reply is None:
        with st.container(border=True):
            st.markdown("**Evidence & context**")
            st.caption("Once you run a real analysis, source records, warnings, assumptions and relevant business details will appear here.")
        return
    intent = str(reply.metadata.get("intent", "unknown"))
    st.caption(f"Latest result · {intent.replace('_', ' ').title()}")
    rows = evidence_rows(reply)
    with st.container(border=True):
        st.markdown(f"**◈ Supporting Evidence**  ·  {len(rows)} records")
        if rows:
            sources = sorted({r["Source"] for r in rows})
            st.caption("Sources: " + ", ".join(sources[:5]))
            with st.expander("View source rows", expanded=False):
                st.dataframe(rows[:15], hide_index=True, use_container_width=True)
                if len(rows) > 15:
                    st.caption(f"Showing 15 of {len(rows)} source references.")
                st.download_button(
                    "Export full evidence CSV",
                    data=evidence_csv(reply).encode("utf-8-sig"),
                    mime="text/csv", file_name="sweaterco_evidence.csv",
                    key="export_" + str(len(_thread()["messages"])),
                )
        else:
            st.caption("No source rows returned for this response.")

    if reply.warnings:
        with st.container(border=True):
            st.markdown(f"**⚠ Risk Warnings**  ·  {len(reply.warnings)}")
            for w in reply.warnings[:5]:
                st.warning(w)
    if reply.assumptions:
        with st.container(border=True):
            st.markdown(f"**◇ Key Assumptions**  ·  {len(reply.assumptions)}")
            for text in reply.assumptions:
                st.markdown("• " + _safe(text))

    # Optional contextual modules are strictly derived from existing A2 results.
    # In particular, the current CSV snapshot does NOT support inventory or
    # shipping-document cards from the concept image.
    for section in contextual_sections(reply):
        with st.container(border=True):
            st.markdown(f"**{section['title']}**")
            if section.get("description"):
                st.caption(section["description"])
            if section.get("rows"):
                with st.expander("View details", expanded=bool(section.get("expanded", False))):
                    st.dataframe(section["rows"], hide_index=True, use_container_width=True)
            for label, value in section.get("facts", []):
                aa, bb = st.columns([1.2, 1])
                with aa:
                    st.caption(label)
                with bb:
                    st.write(value)

    with st.expander("Technical trace", expanded=False):
        st.json({
            "intent": intent,
            "status": reply.metadata.get("status", "not specified"),
            "rule_version": reply.metadata.get("rule_version"),
            "language": reply.metadata.get("language"),
            "source_records": len(reply.evidence),
        })


def main() -> None:
    _init_session()
    ready = _chat_ready()
    _nav_sidebar(ready)
    center, right = st.columns([3.25, 1.55], gap="large", vertical_alignment="top")
    with center:
        _header()
        _workflow_launchpad()
        _structured_helper()
        _conversation()
        _composer(ready)
        st.markdown('<div class="sc-footnote">FACTORY SNAPSHOT · ' + _safe(SNAPSHOT.strftime("%d %b %Y").upper()) + ' · DECISION SUPPORT ONLY · NOT LIVE FACTORY TELEMETRY</div>', unsafe_allow_html=True)
    with right:
        _details(_last_reply())


if __name__ == "__main__":
    main()
