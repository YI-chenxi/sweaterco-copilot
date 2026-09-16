"""Temporary integration shell for the Track 1 user interface."""

import streamlit as st

st.set_page_config(page_title="Factory Co-Pilot", page_icon="Factory", layout="wide")
st.title("Factory Co-Pilot")
st.caption("Track 1 integration shell - data-backed tools and LLM orchestration are coming next.")

st.info(
    "This starter app deliberately runs without an API key. "
    "A will connect deterministic tools; B will connect orchestration; C will replace this shell."
)

question = st.chat_input("Ask about an order, risk, feasibility, or an action...")
if question:
    st.chat_message("user").write(question)
    st.chat_message("assistant").write("The agent integration is not connected yet.")
