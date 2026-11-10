"""Keep a user's reading position when Streamlit rebuilds the workflow launcher."""

from pathlib import Path

import streamlit as st
from streamlit.components.v2 import component


@st.cache_resource(show_spinner=False)
def _bridge(js):
    return component("sweaterco_workflow_scroll", html="", js=js)


def mount_workflow_dock(thread_id, expanded):
    _bridge(Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))(
        key="workflow_scroll_bridge",
        data={"thread": str(thread_id), "expanded": bool(expanded)},
        height=0,
    )
