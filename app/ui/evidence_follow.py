"""Stable answer-ID events from Streamlit's native v2 component bridge."""

from hashlib import sha256
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import streamlit as st


def follow_supported():
    try:
        return callable(getattr(import_module("streamlit.components.v2"), "component", None))
    except ImportError:
        return False


def apply_follow_event(thread, event, *, enabled, panel_open, active_thread):
    if not enabled or not panel_open or not isinstance(event, dict):
        return False
    if thread["id"] != active_thread or event.get("thread") != active_thread:
        return False
    if "follow_token" in thread and event.get("token") != thread["follow_token"]:
        return False
    identity = event.get("answer")
    if not isinstance(identity, str) or not identity:
        return False
    if not any(
        m.get("role") == "assistant" and m.get("ui_reply_id") == identity
        for m in thread["messages"]
    ):
        return False
    if thread.get("selected_reply_id") == identity:
        return False
    thread["selected_reply_id"] = identity
    return True


@st.cache_resource(show_spinner=False)
def _component(js):
    if not follow_supported():
        return None
    return import_module("streamlit.components.v2").component(
        "sweaterco_evidence_follow_" + sha256(js.encode()).hexdigest()[:8],
        js=js,
    )


def follow_token(thread, epoch):
    # Rail-only refreshes share a token so rapid successive scroll events coalesce.
    # Manual actions/full reruns advance the epoch and reject delayed old events.
    if thread.get("follow_token_epoch") != epoch or not thread.get("follow_token"):
        thread["follow_token"] = uuid4().hex
        thread["follow_token_epoch"] = epoch
    return thread["follow_token"]


def mount_follow(thread, enabled, on_change):
    key = "follow_bridge_" + str(thread["id"])
    follow_token(thread, st.session_state.get("follow_epoch", 0))
    component = _component(Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))
    if component is None:
        return
    component(
        key=key,
        height=0,
        data={
            "enabled": enabled,
            "token": thread["follow_token"],
            "epoch": st.session_state.get("follow_epoch", 0),
            "thread": thread["id"],
            "selected": thread.get("selected_reply_id"),
            "selectedLabel": " · 已选中"
            if st.session_state.get("ui_language") == "zh"
            else " · Selected",
            "answers": [m["ui_reply_id"] for m in thread["messages"] if m["role"] == "assistant"],
        },
        on_visible_answer_change=on_change,
    )
