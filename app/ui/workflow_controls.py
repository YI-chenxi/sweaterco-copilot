"""Pure UI interaction helpers for the chat-first Quick Workflows launcher.

This module has no Streamlit or business-tool dependencies and can be tested
without connecting to a model provider.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

from app.ui.language import translate


def workflow_title(workflow: str, values: dict[str, Any], *, language: str = "en") -> str:
    """Build a title only from fields required by *this* workflow.

    Avoid an eager dict of f-strings: it tries to read `order_id` when running
    feasibility and caused the prior KeyError reported from the UI.
    """
    if workflow == "Order Status":
        return f"{translate('Order status', language)} · {values['order_id']}"
    if workflow == "Production Health":
        return f"{translate('Production health', language)} · {values['stage']}"
    if workflow == "Morning Briefing":
        return (
            f"运营晨报 · 前 {values['top_n']} 项"
            if language == "zh"
            else f"Morning briefing · top {values['top_n']}"
        )
    if workflow == "Check Feasibility":
        if language == "zh":
            return (
                f"接单评估 · {values['pieces']} {values['category']} "
                f"交期 {values['requested_delivery_date']}"
            )
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


def display_workflow_message(
    message: dict[str, Any], following: dict[str, Any] | None, language: str
) -> str:
    """Localize generated workflow labels, never arbitrary user questions."""
    content = message["content"]
    request = message.get("ui_workflow_request")
    if not request:
        # Legacy messages lack UI metadata. Require the known deterministic origin
        # and an exact generated-title shape before interpreting their parameters.
        origins = {"Structured workflow · deterministic tools", "快捷工作流 · 业务规则工具"}
        if not following or following.get("origin") not in origins:
            return content
        suffix = r"\s*·\s*(?:Structured workflow|快捷工作流)"
        patterns = (
            ("Order Status", r"(?:Order status|订单状态)\s*·\s*(?P<order_id>[^·\n]+)"),
            ("Production Health", r"(?:Production health|生产健康)\s*·\s*(?P<stage>[A-Z_]+)"),
            (
                "Morning Briefing",
                r"(?:Morning briefing\s*·\s*top |运营晨报\s*·\s*前 )(?P<top_n>\d+)(?: 项)?",
            ),
            (
                "Check Feasibility",
                r"(?:Feasibility|接单评估)\s*·\s*(?P<pieces>\d+) "
                r"(?P<category>[A-Z_]+) (?:by |交期 )"
                r"(?P<requested_delivery_date>\d{4}-\d{2}-\d{2})",
            ),
        )
        for workflow, pattern in patterns:
            matched = re.fullmatch(pattern + suffix, content)
            if matched:
                request = {
                    "workflow": workflow,
                    "values": {key: value.strip() for key, value in matched.groupdict().items()},
                }
                break
    if not request:
        return content
    return (
        workflow_title(request["workflow"], request["values"], language=language)
        + " · "
        + translate("Structured workflow", language)
    )


def display_thread_title(thread: dict[str, Any], language: str) -> str:
    """Localize auto-generated titles without modifying saved/custom titles."""
    title = str(thread["title"])
    messages = thread["messages"]
    if messages and messages[0]["role"] == "user":
        content = messages[0]["content"]
        automatic = content[:36].rstrip() + ("…" if len(content) > 36 else "")
        if title == automatic:
            following = messages[1] if len(messages) > 1 else None
            shown = display_workflow_message(messages[0], following, language)
            return shown[:36].rstrip() + ("…" if len(shown) > 36 else "")
    return translate(title, language) if title == "New conversation" else title
