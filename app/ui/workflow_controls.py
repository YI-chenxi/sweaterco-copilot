"""Pure UI interaction helpers for the chat-first Quick Workflows launcher.

This module has no Streamlit or business-tool dependencies and can be tested
without connecting to a model provider.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


def workflow_title(workflow: str, values: dict[str, Any]) -> str:
    """Build a title only from fields required by *this* workflow.

    Avoid an eager dict of f-strings: it tries to read `order_id` when running
    feasibility and caused the prior KeyError reported from the UI.
    """
    if workflow == "Order Status":
        return f"Order status · {values['order_id']}"
    if workflow == "Production Health":
        return f"Production health · {values['stage']}"
    if workflow == "Morning Briefing":
        return f"Morning briefing · top {values['top_n']}"
    if workflow == "Check Feasibility":
        return (
            f"Feasibility · {values['pieces']} {values['category']} "
            f"by {values['requested_delivery_date']}"
        )
    raise ValueError(f"Unsupported workflow: {workflow}")


def select_workflow(state: MutableMapping[str, Any], name: str) -> None:
    state["workflow"] = name
    state["workflow_panel_expanded"] = True


def toggle_workflow_panel(state: MutableMapping[str, Any]) -> None:
    expanded = not state.get("workflow_panel_expanded", True)
    state["workflow_panel_expanded"] = expanded
    if not expanded:
        state["workflow"] = None  # Also close any active input form.


def collapse_after_response(state: MutableMapping[str, Any]) -> None:
    """Keep a one-line reopen control after any successful assistant reply."""
    state["workflow_panel_expanded"] = False
    state["workflow"] = None
