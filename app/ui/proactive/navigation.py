"""Restore source-page reading position, or focus a confirmed business result."""

from pathlib import Path
from uuid import uuid4

import streamlit as st
from streamlit.components.v2 import component

from .state import init_state


@st.cache_resource(show_spinner=False)
def _navigation(js):
    return component("sweaterco_task_navigation", html="", js=js)


def mount_navigation():
    state = init_state(st.session_state)
    page = st.session_state["ui_page"]
    tab = state["drafts"].get(page + "_section", "")
    target = state.get("return_target", {})
    if target.get("page") != page or (target.get("section") or "") != tab:
        target = {}
    _navigation(Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))(
        key="task_navigation",
        height=0,
        data={
            "scope": state.setdefault("viewport_epoch", uuid4().hex),
            "route": page + "/" + str(tab),
            "page": page,
            "target": target,
        },
    )
