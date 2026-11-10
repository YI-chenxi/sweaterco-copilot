"""Proposal review and return navigation. Business writes stay in Backend B."""

from uuid import uuid4

import streamlit as st

from .locale import tr
from .presentation import detail_grid, heading, parameter_rows, status_panel
from .presentation import readable_time as readable_time
from .state import (
    action_kind,
    action_label,
    call,
    can_decide,
    confirmation_label,
    decide_once,
    destination,
    init_state,
    needs_verification,
    outcome_copy,
)


def model():
    return init_state(st.session_state)


def t(text):
    return tr(text, st.session_state)


def refresh_pending(application):
    result = call(application, "list_pending_actions")
    model()["pending_count"] = len(result["data"].get("items", [])) if result.get("ok") else None


def capture_proposal(result, slot=None, payload=None):
    """Queue a modal only for an explicit new submission, never while replaying history."""
    state = model()
    state["notice_page"] = st.session_state["ui_page"]
    if not result.get("ok") and result.get("error_code") in {
        "INVALID_ARGUMENT",
        "MISSING_FIELD",
        "UNSUPPORTED_CONDITION",
    }:
        values = (payload or {}).get("parameters", {})
        message = "Check the request details and try again."
        if (payload or {}).get("task_type") == "watch" and not str(
            values.get("order_id") or ""
        ).strip():
            message = "Enter an order number to continue."
        state["input_error"] = message
        state.pop("review_dialog", None)
        return
    identifier = result.get("id")
    if not result.get("ok") or result.get("status") != "pending_confirmation" or not identifier:
        return
    page = st.session_state["ui_page"]
    state["selected_action"] = identifier
    state.setdefault("origins", {}).setdefault(
        identifier,
        {
            "page": page,
            "section": state["drafts"].get(page + "_section"),
            "thread": st.session_state.get("active_thread") if page == "copilot" else None,
            "slot": slot,
            "timezone": st.session_state.get("pa_field_watch_zone") if page == "watches" else None,
        },
    )
    state["review_dialog"] = identifier


def dismiss_review():
    model().pop("review_dialog", None)


def open_review(identifier):
    state = model()
    state.pop("pending_decision", None)
    state["selected_action"] = identifier
    dismiss_review()
    state["drafts"]["actions_section"] = "Details"
    st.session_state["pa_field_actions_section"] = "Details"
    st.session_state["ui_page"] = "actions"
    state["return_target"] = {
        "page": "actions",
        "section": "Details",
        "id": None,
        "top": 0,
        "nonce": uuid4().hex,
    }


def check_actions():
    dismiss_review()
    model()["drafts"]["actions_section"] = "History"
    st.session_state["pa_field_actions_section"] = "History"
    st.session_state["ui_page"] = "actions"


def go_back(identifier, item, focus=False):
    state = model()
    target = destination(item)
    origin = state.get("origins", {}).get(identifier, target)
    if target.get("id") and origin.get("timezone"):
        state.setdefault("record_zones", {})[target["id"]] = origin["timezone"]
    selected = target if focus else origin
    page = selected["page"]
    if page == "copilot" and origin.get("thread"):
        if any(thread["id"] == origin["thread"] for thread in st.session_state.get("threads", [])):
            st.session_state["active_thread"] = origin["thread"]
        else:
            page = target["page"]
            selected = target
    if selected.get("section"):
        state["drafts"][page + "_section"] = selected["section"]
        st.session_state["pa_field_" + page + "_section"] = selected["section"]
    state["return_target"] = {
        "page": page,
        "section": selected.get("section"),
        "id": target.get("id") if focus else None,
        "nonce": uuid4().hex,
    }
    state.pop("notice", None)
    dismiss_review()
    st.session_state["ui_page"] = page


def back_label(identifier, item):
    page = model().get("origins", {}).get(identifier, destination(item))["page"]
    return {
        "watches": "Back to Watches & Alerts",
        "briefings": "Back to Morning Briefing",
        "copilot": "Back to conversation",
    }.get(page, "Back")


def target_caption(application, item):
    kind, _, _ = action_kind(item)
    identifier = item.get("command", {}).get("target_id")
    if not identifier:
        return None
    result = call(application, "get_watch" if kind == "watch" else "get_briefing", identifier)
    target = result.get("data", {}).get("item", {}) if result.get("ok") else {}
    if kind == "watch" and target.get("order_id"):
        return t("Order") + " · " + target["order_id"]
    if kind == "briefing" and target.get("local_time"):
        return t("Schedule") + " · " + target["local_time"] + " · " + target["timezone"]
    return t("Target") + " · " + identifier


def _summary(application, item):
    kind, operation, values = action_kind(item)
    target = target_caption(application, item)
    heading(t(action_label(item)), target, resizable=True)
    zone = values.get("timezone") or model().get("origins", {}).get(item.get("id"), {}).get(
        "timezone"
    )
    detail_grid(parameter_rows(item), zone=zone)
    explanation = (
        "Confirming will stop this watch."
        if kind == "watch" and operation == "cancel"
        else "Confirming will cancel this briefing schedule."
        if operation == "cancel"
        else "Confirming will pause this schedule."
        if values.get("enabled") is False
        else "Confirming will resume this schedule."
        if values.get("enabled") is True
        else "Nothing changes until you confirm in Actions."
    )
    st.caption(t(explanation))


def render_review_dialog(application):
    if model().get("input_error"):
        message = model()["input_error"]

        @st.dialog(t("Check your input"), width="small", on_dismiss=dismiss_input_error)
        def input_error():
            st.write(t(message))
            if st.button(t("Back to form"), key="pa_error_return"):
                dismiss_input_error()
                st.rerun()

        input_error()
        return
    identifier = model().get("review_dialog")
    if not identifier:
        return
    from app.ui.dialog_layout import mount_dialog_layout
    from app.ui.language import shared_language

    mount_dialog_layout(shared_language(st.session_state))

    @st.dialog(t("Review your request"), width="large", on_dismiss=dismiss_review)
    def content():
        loaded = call(application, "get_action", identifier)
        if not loaded.get("ok"):
            st.warning(t("Unable to read task data. Refresh to try the read again."))
            st.button(t("Refresh status"), key="pa_modal_refresh")
        else:
            item = loaded["data"]["item"]
            _summary(application, item)
            if not can_decide(item):
                st.info(
                    t(
                        "This request is no longer awaiting confirmation. "
                        "Check its latest status in Actions."
                    )
                )
            if can_decide(item):
                st.caption(
                    t("Closing this window keeps the request pending; it does not cancel it.")
                )
            with st.container(horizontal=True, gap="small"):
                if st.button(
                    t("Review in Actions") + " →",
                    key="pa_notice_review",
                    type="primary",
                    on_click=open_review,
                    args=(identifier,),
                ):
                    st.rerun()
                if st.button(t("Review later"), key="pa_review_later", on_click=dismiss_review):
                    st.rerun()

    content()


def dismiss_input_error():
    model().pop("input_error", None)
    model().pop("notice", None)


def render_outcome(item, identifier):
    """Use the freshly loaded proposal, never a cached success notification."""
    title, explanation = outcome_copy(item)
    with st.container(key="pa_card_outcome"):
        status_panel(
            title,
            explanation,
            state="verification" if needs_verification(item) else item.get("status", "completed"),
        )
        target = destination(item)
        with st.container(horizontal=True, gap="small"):
            st.button(
                t(target["label"] if target.get("id") else back_label(identifier, item)),
                key="pa_return_result",
                on_click=go_back,
                args=(identifier, item, bool(target.get("id"))),
            )
            st.button(t("Stay in Actions"), key="pa_stay_actions", on_click=stay)
        if needs_verification(item):
            st.button(t("Refresh status"), key="pa_refresh_status")


def stay():
    model().pop("notice", None)


def decide(application, identifier, decision):
    with st.spinner(t("Confirming…") if decision == "confirm" else t("Declining…")):
        result = decide_once(application, identifier, decision, model())
    model()["notice_page"] = "actions"
    refresh_pending(application)
    if not result.get("ok"):
        model()["decision_error"] = identifier
    else:
        model().pop("decision_error", None)
    st.rerun()


def render_review_controls(application, item, identifier):
    enabled = can_decide(item) and identifier not in model()["decisions"]
    queued = model().get("pending_decision")
    if not can_decide(item):
        if queued and queued["id"] == identifier:
            model().pop("pending_decision", None)
        render_outcome(item, identifier)
        return
    if model().get("decision_error") == identifier:
        st.warning(t("The request outcome is unknown. Review Actions before submitting again."))
        st.button(t("Refresh status"), key="pa_refresh_status")
    with st.container(horizontal=True, gap="small", key="pa_pair_review_buttons"):
        for operation, label in (
            ("confirm", confirmation_label(item)),
            ("reject", "Decline request"),
        ):
            busy = bool(queued and queued["id"] == identifier and queued["decision"] == operation)
            shown = "Confirming…" if operation == "confirm" else "Declining…"
            if st.button(
                t(shown if busy else label),
                key="pa_decide_" + operation + "_" + identifier,
                disabled=not enabled or bool(queued),
                type="primary" if operation == "confirm" else "secondary",
            ):
                model()["pending_decision"] = {"id": identifier, "decision": operation}
                st.rerun()
    if queued and queued["id"] == identifier:
        model().pop("pending_decision", None)
        decide(application, identifier, queued["decision"])


def render_notice(page):
    state = model()
    result = state.get("notice")
    if (
        not result
        or page == "actions"
        or state.get("input_error")
        or state.get("notice_page") != page
    ):
        return
    if not result.get("ok"):
        code = result.get("error_code")
        st.warning(
            t(
                "Please check the input fields."
                if code in {"INVALID_ARGUMENT", "MISSING_FIELD"}
                else "The request outcome is unknown. Review Actions before submitting again."
            )
        )
        if code not in {"INVALID_ARGUMENT", "MISSING_FIELD"}:
            st.button(t("Check Actions"), key="pa_check_actions", on_click=check_actions)
    elif result.get("status") == "pending_confirmation" and not state.get("review_dialog"):
        st.caption(t("Your request is saved and awaiting review."))
        st.button(
            t("Review in Actions"),
            key="pa_notice_review",
            on_click=open_review,
            args=(result["id"],),
            icon=":material/fact_check:",
        )
