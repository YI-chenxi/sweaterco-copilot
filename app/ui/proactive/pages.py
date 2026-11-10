"""Frontend C: task pages. Writes always pass through Backend B proposals."""

import html
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st

from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.ui.language import shared_language
from app.ui.localized_date import localized_date

from . import journey
from .locale import tr
from .presentation import detail_grid, display_value, heading, parameter_rows
from .state import (
    action_label,
    bind_scope,
    call,
    can_decide,
    command,
    init_state,
    propose_once,
    public_data,
    result_reply,
    status_label,
)

ZONES = (
    "Asia/Singapore",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Kolkata",
    "Europe/London",
    "Europe/Paris",
    "America/New_York",
    "America/Los_Angeles",
    "Australia/Sydney",
    "UTC",
)
ZONE_NAMES = {
    "Asia/Singapore": ("Singapore", "新加坡"),
    "Asia/Shanghai": ("China · Shanghai", "中国 · 上海"),
    "Asia/Tokyo": ("Japan · Tokyo", "日本 · 东京"),
    "Asia/Kolkata": ("India · Kolkata", "印度 · 加尔各答"),
    "Europe/London": ("UK · London", "英国 · 伦敦"),
    "Europe/Paris": ("France · Paris", "法国 · 巴黎"),
    "America/New_York": ("US · New York", "美国 · 纽约"),
    "America/Los_Angeles": ("US · Los Angeles", "美国 · 洛杉矶"),
    "Australia/Sydney": ("Australia · Sydney", "澳大利亚 · 悉尼"),
    "UTC": ("Coordinated Universal Time", "协调世界时"),
}


def zone_label(zone, day, language):
    offset = datetime.combine(day, time(12), ZoneInfo(zone)).utcoffset()
    minutes = int(offset.total_seconds() // 60)
    hours, remainder = divmod(abs(minutes), 60)
    suffix = f":{remainder:02d}" if remainder else ""
    sign = "+" if minutes >= 0 else "−"
    name = ZONE_NAMES.get(zone, (zone, zone))[language == "zh"]
    return f"{name} (UTC{sign}{hours}{suffix})"


def t(text):
    return tr(text, st.session_state)


def model():
    return init_state(st.session_state)


def remember(name, widget):
    model()["drafts"][name] = st.session_state[widget]


def field(kind, label, name, default, **options):
    key = "pa_field_" + name
    if key not in st.session_state:
        st.session_state[key] = model()["drafts"].get(name, default)
    if "options" in options and st.session_state[key] not in options["options"]:
        st.session_state[key] = default
    if kind == "date_input":
        return localized_date(
            t(label),
            key,
            st.session_state[key],
            shared_language(st.session_state),
            lambda: remember(name, key),
        )
    if "format_func" in options and "options" in options:
        labels = {value: options["format_func"](value) for value in options["options"]}
        options["format_func"] = lambda value: labels.get(value, str(value))
    return getattr(st, kind)(
        t(label),
        key=key,
        on_change=remember,
        args=(name, key),
        **options,
    )


def section(page, choices, default):
    return field(
        "radio",
        "Select",
        page + "_section",
        default,
        options=choices,
        format_func=t,
        horizontal=True,
        label_visibility="collapsed",
    )


def select_evidence(page, kind, identifier):
    model().setdefault("selection", {})[page] = (kind, identifier)


def review(identifier):
    journey.open_review(identifier)


def error(result, reading=False):
    code = result.get("error_code")
    message = (
        "Service not connected"
        if code == "NOT_CONNECTED"
        else "Access denied. Sign in to your authorized workspace."
        if code == "UNAUTHORIZED"
        else "Please check the input fields."
        if code in {"MISSING_FIELD", "INVALID_ARGUMENT"}
        else "The target has changed or this request is no longer available. Refresh the page."
        if code in {"CONFLICT", "NOT_FOUND", "EXPIRED", "ALREADY_EXECUTED"}
        else "Unable to read task data. Refresh to try the read again."
        if reading
        else "The request outcome is unknown. Review Actions before submitting again."
    )
    st.warning(t(message))


def propose(application, payload, slot):
    model()["preparing_request"] = {"payload": public_data(payload), "slot": slot}
    st.rerun()


def dispatch_proposal(application):
    queued = model().pop("preparing_request", None)
    if not queued:
        return
    with st.spinner(t("Preparing request…")):
        payload = queued["payload"]
        if (
            payload.get("task_type") == "watch"
            and payload.get("command_type") == "create"
            and not str(payload.get("parameters", {}).get("order_id") or "").strip()
        ):
            result = {"ok": False, "error_code": "MISSING_FIELD", "data": {}}
            model()["notice"] = result
        else:
            result = propose_once(application, payload, model(), queued["slot"])
    journey.capture_proposal(result, queued["slot"], payload)
    journey.refresh_pending(application)
    st.rerun()


def reset_submission(slot):
    model()["submissions"].pop(slot, None)
    model().pop("notice", None)
    model().pop("return_target", None)


def submit(application, payload, slot, label="Create request"):
    old = model()["submissions"].get(slot)
    blocked = bool(old and old["state"] == "unknown")
    queued = model().get("preparing_request")
    shown = "Preparing request…" if queued and queued["slot"] == slot else label
    if st.button(
        t(shown), key="pa_submit_" + slot, type="primary", disabled=blocked or bool(queued)
    ):
        propose(application, payload, slot)


def parameters(item):
    zone = item.get("command", {}).get("parameters", {}).get("timezone") or model().get(
        "origins", {}
    ).get(item.get("id"), {}).get("timezone")
    detail_grid(parameter_rows(item), zone=zone)


def operation_columns(identity):
    """Scope compact button geometry to this row, never the page's main columns."""
    with st.container(key="pa_pair_" + identity):
        return st.columns(2, gap="small")


def schedule_form(application, existing=None):
    item = existing or {}
    prefix = "schedule_" + item.get("id", "create")
    operation = "update" if existing else "create"
    with st.expander(
        t("Edit schedule" if existing else "Create a schedule"),
        expanded=not existing
        and not (
            model().get("return_target", {}).get("page") == "briefings"
            and model()["return_target"].get("id")
        ),
    ):
        st.caption(t("Schedule changes need confirmation."))
        left, right = st.columns(2)
        with left:
            days = field(
                "number_input",
                "Repeat every (days)",
                prefix + "_days",
                item.get("interval_days", 1),
                min_value=1,
                step=1,
            )
            start = field(
                "date_input",
                "Start date",
                prefix + "_start",
                date.fromisoformat(item["start_date"]) if existing else date.today(),
            )
        with right:
            at = field(
                "time_input",
                "Time of day",
                prefix + "_time",
                time.fromisoformat(item["local_time"]) if existing else time(8),
            )
            zones = tuple(dict.fromkeys((*ZONES, item.get("timezone", ZONES[0]))))
            zone = field(
                "selectbox",
                "Time zone",
                prefix + "_zone",
                item.get("timezone", ZONES[0]),
                options=zones,
                format_func=lambda z: zone_label(z, start, shared_language(st.session_state)),
            )
        values = {
            "interval_days": days,
            "local_time": at.strftime("%H:%M"),
            "timezone": zone,
            "start_date": start.isoformat(),
        }
        if existing:
            values["enabled"] = field("checkbox", "Enabled", prefix + "_enabled", item["enabled"])
        submit(
            application,
            command(
                "briefing",
                operation,
                values,
                target=item.get("id"),
                language=shared_language(st.session_state),
            ),
            prefix,
            "Prepare changes" if existing else "Create request",
        )


def briefing_page(application, orchestrator):
    tab = section("briefings", ["Instant briefing", "Schedules", "History"], "Instant briefing")
    loaded = call(application, "list_briefings")
    data = loaded.get("data", {}) if loaded.get("ok") else {}
    history = sorted(data.get("history", []), key=lambda r: r["executed_at"], reverse=True)
    if tab == "Instant briefing":
        with st.container(border=True, key="pa_card_instant"):
            count = field(
                "number_input",
                "Priority findings",
                "instant_count",
                5,
                min_value=1,
                max_value=20,
                step=1,
            )
            if st.button(
                t("Generate now"), key="pa_instant", type="primary", icon=":material/wb_sunny:"
            ):
                try:
                    reply = orchestrator.handle(
                        IntentDecision(
                            Intent.MORNING_BRIEFING,
                            {"top_n": count},
                            language=ResponseLanguage(shared_language(st.session_state)),
                        )
                    )
                    model()["manual"] = {"id": str(uuid4()), "reply": reply}
                    select_evidence("briefings", "manual", model()["manual"]["id"])
                except Exception:
                    error({"error_code": "STORAGE_ERROR"}, reading=True)
            manual = model().get("manual")
            if manual:
                st.caption(t("Not archived. This result stays in this session."))
                st.markdown(manual["reply"].text)
                if st.button(t("View evidence"), key="pa_manual_evidence"):
                    select_evidence("briefings", "manual", manual["id"])
            st.markdown("**" + t("Latest saved briefing") + "**")
            if history:
                st.caption(journey.readable_time(history[0]["executed_at"]))
                if st.button(t("View result"), key="pa_latest_saved"):
                    select_evidence("briefings", "run", history[0]["id"])
            elif loaded.get("ok"):
                st.caption(t("No saved briefings yet."))
    elif loaded.get("ok") and tab == "Schedules":
        schedule_form(application)
        if not data.get("items"):
            st.info(t("No schedules yet."))
        for item in data.get("items", []):
            with st.container(border=True, key="pa_card_schedule_" + item["id"]):
                status = (
                    "Cancelled"
                    if item.get("cancelled_at")
                    else ("Enabled" if item["enabled"] else "Paused")
                )
                heading(
                    t("Briefing schedule"), item["local_time"] + " · " + item["timezone"], status
                )
                detail_grid(
                    [
                        ("Repeat every (days)", item["interval_days"]),
                        ("Next run", item.get("next_run_at")),
                    ],
                    zone=item["timezone"],
                )
                if item.get("cancelled_at"):
                    continue
                schedule_form(application, item)
                toggle_slot = "toggle_" + item["id"] + str(item["enabled"])
                queued_slot = model().get("preparing_request", {}).get("slot")
                a, b = operation_columns("schedule_" + item["id"])
                with a:
                    if st.button(
                        t(
                            "Preparing request…"
                            if queued_slot == toggle_slot
                            else "Pause"
                            if item["enabled"]
                            else "Enable"
                        ),
                        key="pa_toggle_" + item["id"],
                        disabled=bool(queued_slot),
                    ):
                        propose(
                            application,
                            command(
                                "briefing",
                                "update",
                                {"enabled": not item["enabled"]},
                                target=item["id"],
                                language=shared_language(st.session_state),
                            ),
                            toggle_slot,
                        )
                with b:
                    if st.button(
                        t(
                            "Preparing request…"
                            if queued_slot == "cancel_" + item["id"]
                            else "Cancel schedule"
                        ),
                        key="pa_cancel_" + item["id"],
                        disabled=bool(queued_slot),
                    ):
                        propose(
                            application,
                            command(
                                "briefing",
                                "cancel",
                                {},
                                target=item["id"],
                                language=shared_language(st.session_state),
                            ),
                            "cancel_" + item["id"],
                        )
        st.caption(t("Scheduled time is not a guarantee of a deployed runner."))
    elif loaded.get("ok"):
        if not history:
            st.info(t("No briefing history yet."))
        for item in history:
            with st.container(border=True, key="pa_card_run_" + item["id"]):
                zone = next(
                    (
                        s["timezone"]
                        for s in data.get("items", [])
                        if s["id"] == item["schedule_id"]
                    ),
                    None,
                )
                heading(
                    t("Morning Briefing"),
                    status="Success" if item["status"] == "success" else "Failed",
                )
                detail_grid(
                    [
                        ("Scheduled for", item["scheduled_for"]),
                        ("Executed at", item["executed_at"]),
                    ],
                    zone=zone,
                )
                if st.button(t("View result"), key="pa_run_" + item["id"]):
                    select_evidence("briefings", "run", item["id"])
    if not loaded.get("ok"):
        error(loaded, reading=True)
    selected = model().get("selection", {}).get("briefings")
    manual = model().get("manual")
    if manual and selected == ("manual", manual["id"]):
        return {"title": "Briefing evidence", "id": manual["id"], "reply": manual["reply"]}
    chosen = next((r for r in history if selected == ("run", r["id"])), None)
    if chosen:
        return {
            "title": "Briefing evidence",
            "id": chosen["id"],
            "reply": result_reply(chosen.get("result", {})),
            "text": chosen.get("result", {}).get("text", ""),
            "facts": [("Executed at", chosen["executed_at"])],
        }
    return None


def watch_form(application):
    with st.expander(
        t("Create a watch"),
        expanded=not (
            model().get("return_target", {}).get("page") == "watches"
            and model()["return_target"].get("id")
        ),
    ):
        order = field("text_input", "Order number", "watch_order", "ORD-057")
        condition = field(
            "selectbox",
            "Watch condition",
            "watch_condition",
            "no_progress_by_deadline",
            options=["no_progress_by_deadline", "status_equals"],
            format_func=lambda c: t(
                "No progress by deadline"
                if c == "no_progress_by_deadline"
                else "Order reaches a status"
            ),
        )
        interval = field(
            "number_input", "Check every (minutes)", "watch_interval", 60, min_value=1, step=1
        )
        values = {
            "order_id": order.strip().upper(),
            "condition_type": condition,
            "check_interval_minutes": interval,
        }
        if condition == "status_equals":
            values["expected_status"] = field(
                "selectbox",
                "Expected status",
                "watch_status",
                "COMPLETE",
                options=["IN_PROGRESS", "COMPLETE"],
                format_func=t,
            )
        else:
            day = field(
                "date_input", "Deadline date", "watch_day", date.today() + timedelta(days=1)
            )
            at = field("time_input", "Deadline time", "watch_time", time(17))
            zone = field(
                "selectbox",
                "Time zone",
                "watch_zone",
                ZONES[0],
                options=ZONES,
                format_func=lambda z: zone_label(z, day, shared_language(st.session_state)),
            )
            values["deadline_at"] = (
                datetime.combine(day, at, ZoneInfo(zone)).astimezone(ZoneInfo("UTC")).isoformat()
            )
        submit(
            application,
            command("watch", "create", values, language=shared_language(st.session_state)),
            "watch_create",
        )


def watch_page(application):
    tab = section("watches", ["Active watches", "Alerts", "Checks"], "Active watches")
    loaded = call(application, "list_watches")
    if not loaded.get("ok"):
        error(loaded, reading=True)
        return None
    data = loaded["data"]
    st.caption(t("Snapshot data. Results appear after the external checker runs."))
    if tab == "Active watches":
        watch_form(application)
        if not data.get("items"):
            st.info(t("No active watches."))
        for item in data.get("items", []):
            with st.container(border=True, key="pa_card_watch_" + item["id"]):
                condition = (
                    "No progress by deadline"
                    if item["condition_type"] == "no_progress_by_deadline"
                    else "Order reaches a status"
                )
                heading(
                    item["order_id"],
                    t(condition),
                    "Cancelled" if item.get("cancelled") else "Enabled",
                )
                rows = [("Check every (minutes)", item["check_interval_minutes"])]
                rows.append(
                    ("Deadline", item["deadline_at"])
                    if item.get("deadline_at")
                    else (
                        "Expected status",
                        t(
                            "Complete"
                            if item.get("expected_status") == "COMPLETE"
                            else "In progress"
                        ),
                    )
                )
                detail_grid(rows, zone=model().get("record_zones", {}).get(item["id"]))
                queued_slot = model().get("preparing_request", {}).get("slot")
                if st.button(
                    t(
                        "Preparing request…"
                        if queued_slot == "cancel_watch_" + item["id"]
                        else "Cancel watch"
                    ),
                    key="pa_cancel_watch_" + item["id"],
                    disabled=bool(queued_slot),
                ):
                    propose(
                        application,
                        command(
                            "watch",
                            "cancel",
                            {},
                            target=item["id"],
                            language=shared_language(st.session_state),
                        ),
                        "cancel_watch_" + item["id"],
                    )
    else:
        rows = data.get("alerts" if tab == "Alerts" else "checks", [])
        if not rows:
            st.info(t("No alerts yet." if tab == "Alerts" else "No checks yet."))
        for item in sorted(rows, key=lambda r: r["timestamp"], reverse=True):
            with st.container(border=True, key="pa_card_check_" + item["id"]):
                outcome = {
                    "matched": "Matched",
                    "not_matched": "Not matched",
                    "unavailable": "Unavailable",
                }.get(item.get("outcome"), "Unknown")
                watch = next((w for w in data.get("items", []) if w["id"] == item["watch_id"]), {})
                heading(watch.get("order_id", t("Order watch")), status=outcome)
                detail_grid(
                    [("Observed at", item["timestamp"])],
                    zone=model().get("record_zones", {}).get(item["watch_id"]),
                )
                if st.button(t("View evidence"), key="pa_check_" + item["id"]):
                    select_evidence("watches", "check", item["id"])
                with st.expander(t("Record details")):
                    st.caption(t("Target") + " · " + item["watch_id"])
                    st.caption(item["id"])
    selected = model().get("selection", {}).get("watches")
    chosen = next(
        (
            r
            for r in data.get("checks", []) + data.get("alerts", [])
            if selected == ("check", r["id"])
        ),
        None,
    )
    if chosen:
        return {
            "title": "Watch evidence",
            "id": chosen["id"],
            "reply": result_reply(chosen),
            "facts": [("Observed at", chosen["timestamp"]), ("Target", chosen["watch_id"])],
            "observation": chosen.get("observed_value"),
        }
    return None


def actions_page(application):
    tab = section("actions", ["Pending", "History", "Details"], "Pending")
    pending, history = (
        call(application, "list_pending_actions"),
        call(application, "list_action_history"),
    )
    left, right = st.columns(2)
    left.metric(t("Pending"), len(pending["data"].get("items", [])) if pending.get("ok") else "—")
    right.metric(
        t("All operations"), len(history["data"].get("items", [])) if history.get("ok") else "—"
    )
    for result in (pending, history):
        if not result.get("ok"):
            error(result, reading=True)
    if tab != "Details":
        result = pending if tab == "Pending" else history
        if result.get("ok"):
            rows = result["data"].get("items", [])
            if not rows:
                st.info(t("No pending actions." if tab == "Pending" else "No action history yet."))
            for item in reversed(rows):
                with st.container(border=True, key="pa_card_action_" + item["id"]):
                    heading(
                        t(action_label(item)),
                        t("Request reference") + " · " + item["id"][:8],
                        status_label(item),
                    )
                    st.button(
                        t("Review"),
                        key="pa_review_" + item["id"],
                        on_click=review,
                        args=(item["id"],),
                    )
    identifier = model().get("selected_action")
    if not identifier:
        if tab == "Details":
            st.info(t("Select an action to review."))
        return None
    loaded = call(application, "get_action", identifier)
    if not loaded.get("ok"):
        if tab == "Details":
            error(loaded, reading=True)
        return None
    item = loaded["data"]["item"]
    model().setdefault("action_snapshots", {})[identifier] = public_data(item)
    group = next((r for r in history.get("data", {}).get("items", []) if r["id"] == identifier), {})
    if tab == "Details":
        with st.container(border=True, key="pa_card_review"):
            order_id = item.get("command", {}).get("parameters", {}).get("order_id")
            target = journey.target_caption(application, item)
            heading(t(action_label(item)) + (" · " + order_id if order_id else ""), target)
            origin = model().get("origins", {}).get(identifier)
            if origin:
                source = {
                    "briefings": "Morning Briefing",
                    "watches": "Watches & Alerts",
                    "copilot": "AI Co-Pilot",
                }.get(origin["page"], "Actions")
                st.caption(t("Requested from") + " · " + t(source))
            if can_decide(item):
                st.caption(t("Nothing changes until you confirm in Actions."))
            st.markdown(
                '<div class="pa-section-title">' + t("Request details") + "</div>",
                unsafe_allow_html=True,
            )
            parameters(item)
            journey.render_review_controls(application, item, identifier)
            if can_decide(item) or journey.destination(item).get("id"):
                with st.container(key="pa_back_bar"):
                    st.button(
                        t(journey.back_label(identifier, item)),
                        key="pa_back_source",
                        icon=":material/arrow_back:",
                        on_click=journey.go_back,
                        args=(identifier, item),
                    )
            with st.expander(t("Request record")):
                st.caption(t("Request reference") + " · " + identifier)
                detail_grid([("Created", item["created_at"]), ("Expires", item["expires_at"])])
        st.markdown("**" + t("Timeline") + "**")
        for event in group.get("events", []):
            st.write("• " + t(status_label(event)))
            stamp = event.get("timestamp") or event.get("created_at") or event.get("at")
            if stamp:
                st.caption(journey.readable_time(stamp))
    return {
        "title": "Action details",
        "id": identifier,
        "proposal": item,
        "facts": [("Created", item["created_at"]), ("Expires", item["expires_at"])],
    }


def render_page(page, application, orchestrator):
    scope = bind_scope(st.session_state, application)
    title, subtitle = {
        "briefings": ("Morning Briefing", "A clear start to your day."),
        "watches": ("Watches & Alerts", "Keep an eye on the orders that matter."),
        "actions": ("Actions", "Review first. Stay in control."),
    }[page]
    st.markdown(
        '<div class="pa-hero"><h1>'
        + html.escape(t(title))
        + "</h1><p>"
        + html.escape(t(subtitle))
        + "</p></div>",
        unsafe_allow_html=True,
    )
    if st.button(t("Refresh"), key="pa_refresh_" + page, icon=":material/refresh:"):
        st.rerun()
    if not scope:
        st.info(t("Proactive tasks need a verified account and workspace."))
        st.caption(t("No task data was loaded. Your chat and Quick Workflows are still available."))
        if page != "briefings":
            return None
    if scope and scope[0] == "demo-operator":
        st.caption(t("Local demo workspace · not a signed-in account"))
    if scope:
        previous_count = model().get("pending_count")
        journey.refresh_pending(application)
        if previous_count != model().get("pending_count"):
            st.rerun()
    journey.render_notice(page)
    if page == "briefings":
        details = briefing_page(application, orchestrator)
    elif page == "watches":
        details = watch_page(application)
    else:
        details = actions_page(application)
    if scope:
        dispatch_proposal(application)
    return details


def render_context(details, evidence_renderer):
    if not details:
        st.info(t("Select a saved result to inspect its evidence."))
        return
    st.markdown("**" + t(details["title"]) + "**")
    st.caption(details["id"])
    for label, value in details.get("facts", []):
        st.caption(t(label) + " · " + display_value(value))
    if details.get("text"):
        st.markdown(details["text"])
    if details.get("proposal"):
        item = details["proposal"]
        st.write(t(status_label(item)))
        parameters(item)
        receipt = (item.get("result") or {}).get("receipt")
        if receipt:
            st.markdown("**" + t("Business result") + "**")
            st.write(str(receipt.get("status", "")))
            if receipt.get("id"):
                st.caption(receipt["id"])
    for name, value in public_data(details.get("observation") or {}).items():
        if value is None or isinstance(value, (str, int, float, bool)):
            st.write(t(name) + " · " + display_value(value))
    if details.get("reply") is not None:
        evidence_renderer(details["reply"], "pa_" + details["id"])


def render_chat_result(result, reply_id):
    # Only inspect the proposal already created by the shared AgentService.
    result = public_data(result)
    item = result.get("data", {}).get("proposal", {})
    identifier = result.get("id") or item.get("id")
    item = model().get("action_snapshots", {}).get(identifier, item)
    st.markdown("**" + t(action_label(item)) + "**")
    if result.get("ok"):
        st.write(t(status_label(item or result)))
        if item.get("status", result.get("status")) == "pending_confirmation":
            st.caption(t("A request is ready for review. Nothing has changed yet."))
    else:
        error(result)
    if item:
        parameters(item)
    if identifier:
        st.button(
            t("Review in Actions" if can_decide(item) else "View in Actions"),
            key="pa_chat_review_" + reply_id,
            on_click=review,
            args=(identifier,),
            icon=":material/fact_check:",
        )
    if result.get("status") == "needs_clarification":
        st.write(result.get("message", ""))
