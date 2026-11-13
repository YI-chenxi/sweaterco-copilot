"""Keep unsent native-composer text in its originating browser chat only."""

from hashlib import sha256
from pathlib import Path

import streamlit as st
from streamlit.components.v2 import component


def apply_draft_event(thread, event):
    if not isinstance(event, dict) or event.get("thread") != thread["id"]:
        return False
    if event.get("token") != thread.get("composer_token"):
        return False
    sequence, text = event.get("sequence"), event.get("text")
    if type(sequence) not in {float, int} or type(text) is not str:
        return False
    if sequence <= thread.get("composer_sequence", -1):
        return False
    thread["composer_sequence"] = sequence
    thread["composer_draft"] = text
    draft = thread.get("speech_draft")
    if draft is not None:
        if text.strip():
            draft["text"] = text
        else:
            thread.pop("speech_draft", None)
    return True


@st.cache_resource(show_spinner=False)
def _bridge(js):
    return component("sweaterco_composer_draft_" + sha256(js.encode()).hexdigest()[:8], js=js)


def mount_draft_bridge(thread, widget_key, bridge_key, on_change):
    _bridge(Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))(
        key=bridge_key,
        height=0,
        data={"thread": thread["id"], "token": thread["composer_token"], "widgetKey": widget_key},
        on_draft_change=on_change,
    )
