"""Local-only window geometry controls for the native workflow dialog."""

import json
from pathlib import Path

import streamlit as st


def mount_dialog_layout(language: str) -> None:
    source = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
    source = source.replace(
        "__SC_DIALOG_LANGUAGE__", json.dumps("zh" if language == "zh" else "en")
    )
    st.html("<script>" + source + "</script>", unsafe_allow_javascript=True)
