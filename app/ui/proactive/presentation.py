"""Shared, escaped display components. Stored business records remain unchanged."""

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from .locale import tr

DEFAULT_ZONE = "Asia/Singapore"
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def t(text):
    return tr(text, st.session_state)


def readable_time(value, zone=None):
    if value is None or value == "":
        return "—"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        chosen = zone or DEFAULT_ZONE
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(ZoneInfo(chosen))
        offset = stamp.strftime("%z")
        suffix = "UTC" + offset[:3] + ":" + offset[3:] if offset else ""
        city = chosen.rsplit("/", 1)[-1].replace("_", " ") if offset else ""
        return stamp.strftime("%Y-%m-%d %H:%M:%S") + (
            " · " + city + " (" + suffix + ")" if suffix else ""
        )
    except (ValueError, TypeError, KeyError):
        return str(value)


def display_value(value, zone=None):
    if isinstance(value, str) and _STAMP.match(value):
        return readable_time(value, zone)
    if value is True:
        return t("Enabled")
    if value is False:
        return t("Paused")
    return str(value) if value is not None else "—"


def detail_grid(rows, *, zone=None):
    cells = []
    for label, value in rows:
        cells.append(
            '<div class="pa-fact"><dt>'
            + html.escape(t(label))
            + "</dt><dd>"
            + html.escape(display_value(value, zone))
            + "</dd></div>"
        )
    st.markdown('<dl class="pa-facts-grid">' + "".join(cells) + "</dl>", unsafe_allow_html=True)


def parameter_rows(item):
    params = item.get("command", {}).get("parameters", item.get("parameters", {}))
    labels = {
        "interval_days": "Repeat every (days)",
        "local_time": "Time of day",
        "timezone": "Time zone",
        "start_date": "Start date",
        "enabled": "Schedule status",
        "order_id": "Order number",
        "condition_type": "Watch condition",
        "check_interval_minutes": "Check every (minutes)",
        "deadline_at": "Deadline",
        "expected_status": "Expected status",
    }
    values = {
        "no_progress_by_deadline": "No progress by deadline",
        "status_equals": "Order reaches a status",
        "IN_PROGRESS": "In progress",
        "COMPLETE": "Complete",
    }
    order = [
        "order_id",
        "condition_type",
        "expected_status",
        "deadline_at",
        "check_interval_minutes",
        "interval_days",
        "local_time",
        "timezone",
        "start_date",
        "enabled",
    ]
    keys = [key for key in order if key in params] + [key for key in params if key not in order]
    return [
        (
            labels.get(name, name),
            t(values.get(params[name], params[name]))
            if isinstance(params[name], str)
            else params[name],
        )
        for name in keys
        if name not in {"idempotency_key", "request_key", "token", "password", "fingerprint"}
    ]


def heading(title, subtitle=None, status=None, *, resizable=False):
    badge = '<span class="pa-status-badge">' + html.escape(t(status)) + "</span>" if status else ""
    description = "<p>" + html.escape(str(subtitle)) + "</p>" if subtitle else ""
    marker = '<span class="sc-resizable-dialog-marker"></span>' if resizable else ""
    st.markdown(
        '<header class="pa-record-heading">'
        + marker
        + "<div><h3>"
        + html.escape(str(title))
        + "</h3>"
        + description
        + "</div>"
        + badge
        + "</header>",
        unsafe_allow_html=True,
    )


def status_panel(title, explanation, *, state="completed"):
    st.markdown(
        '<section class="pa-status-panel pa-status-'
        + html.escape(state, quote=True)
        + '"><span class="pa-status-dot" aria-hidden="true"></span><div><strong>'
        + html.escape(t(title))
        + "</strong><p>"
        + html.escape(t(explanation))
        + "</p></div></section>",
        unsafe_allow_html=True,
    )
