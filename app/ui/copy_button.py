"""One-click local clipboard control; answer text remains component data."""

from pathlib import Path

import streamlit as st
from streamlit.components.v2 import component


@st.cache_resource(show_spinner=False)
def _copy_component():
    return component(
        "sweaterco_copy_analysis",
        html='<button type="button" class="copy-analysis"></button>'
        '<span class="copy-status" role="status" aria-live="polite"></span>',
        css="""
        :host{display:block;width:34px;height:34px;overflow:visible}
        button{position:relative;display:flex;align-items:center;justify-content:center;width:32px;height:32px;
          padding:0;background:transparent;border:0;border-radius:9px;color:var(--sc-copy-ink,#538577);cursor:pointer}
        button:hover{background:var(--sc-copy-hover,#e5f2eb);color:var(--sc-copy-active,#087f78)}
        button:focus-visible{outline:2px solid var(--sc-copy-active,#087f78);outline-offset:-2px}
        button::after{content:attr(aria-label);position:absolute;right:0;bottom:calc(100% + 7px);
          padding:7px 10px;border:1px solid var(--sc-line,#b6d6d0);border-radius:12px;
          background:var(--sc-control,#eaf5f1);color:var(--sc-ink,#163c43);
          font:12px/1.4 "Segoe UI",sans-serif;white-space:nowrap;pointer-events:none;
          opacity:0;box-shadow:0 5px 16px #00000018;z-index:20}
        button:hover::after,button:focus-visible::after{opacity:1}
        button svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.7;
          stroke-linecap:round;stroke-linejoin:round}
        .copy-status{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
        """,
        js=Path(__file__).with_suffix(".js").read_text(encoding="utf-8"),
    )


def copy_analysis(text: str, language: str, identity: str) -> None:
    _copy_component()(
        key="copy_analysis_" + identity,
        data={"text": text, "language": language},
        width=34,
        height=34,
    )
