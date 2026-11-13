"""Page-language calendar; native browser date pickers cannot select an app locale."""

from datetime import date
from pathlib import Path

import streamlit as st
from streamlit.components.v2 import component


@st.cache_resource(show_spinner=False)
def _calendar():
    return component(
        "sweaterco_localized_date",
        html='<div class="date-field"></div>',
        css=Path(__file__).with_suffix(".css").read_text(encoding="utf-8"),
        js=Path(__file__).with_suffix(".js").read_text(encoding="utf-8"),
    )


def apply_date(key, component_key, on_change):
    raw = st.session_state.get(component_key, {}).get("value")
    try:
        selected = date.fromisoformat(raw)
    except (TypeError, ValueError):
        return
    st.session_state[key] = selected
    on_change()


def localized_date(label, key, value, language, on_change):
    component_key = key + "_calendar"
    _calendar()(
        key=component_key,
        data={"label": label, "value": value.isoformat(), "language": language},
        default={"value": value.isoformat()},
        on_value_change=lambda: apply_date(key, component_key, on_change),
        width="stretch",
        height="content",
    )
    return st.session_state.get(key, value)
