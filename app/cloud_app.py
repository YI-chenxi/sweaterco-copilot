"""Streamlit Cloud entry: native HTTP cookie routes plus the approved account UI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from app.accounts.session.browser_http import browser_routes  # noqa: E402
from app.ui.accounts.cloud_runtime import get_runtime  # noqa: E402

app = st.App(
    Path(__file__).with_name("ui") / "cloud_workspace.py",
    routes=browser_routes(get_runtime),
)
