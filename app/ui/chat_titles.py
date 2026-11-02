"""Short, local conversation labels derived from the first exchange only.

Titles are presentation data. They never call a model, modify task memory, or
derive a business conclusion that the backend has not supplied.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from app.agent.contracts import AgentReply

TITLE_WIDTH = 48


def clean_request_text(text: str) -> str:
    """Remove the legacy UI origin suffix while preserving the full request."""
    return re.sub(r"\s*·\s*Structured workflow$", "", text, flags=re.IGNORECASE).strip()


def compact_title(text: str, limit: int = TITLE_WIDTH) -> str:
    """Limit visual length, counting wide Chinese characters as two cells."""
    text = re.sub(r"\s+", " ", text).strip()
    text = clean_request_text(text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    width = 0
    for index, char in enumerate(text):
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if width > limit:
            short = text[: max(0, index - 1)].rstrip()
            # Prefer a whole word when the cut is inside an English word.
            if index < len(text) and text[index].isascii() and text[index].isalnum():
                boundary = short.rfind(" ")
                if boundary > len(short) // 2:
                    short = short[:boundary]
            return short.rstrip(" ,;·+") + "…"
    return text


def _task_label(intent: str, slots: Mapping[str, Any], chinese: bool) -> str:
    if intent == "order_status":
        order = str(slots.get("order_id") or "").strip()
        return f"{order} {'订单状态' if chinese else 'order status'}".strip()
    if intent == "production_normality":
        stage = str(slots.get("stage") or "").strip()
        if chinese:
            stage = {
                "KNITTING": "针织",
                "ASSEMBLY": "缝合",
                "WASHING": "洗水",
                "PACKING": "包装",
            }.get(stage, stage)
        return f"{stage} {'生产检查' if chinese else 'production check'}".strip()
    if intent == "morning_briefing":
        return "工厂风险晨报" if chinese else "Morning briefing"
    if intent == "order_feasibility":
        detail = " ".join(str(slots[key]) for key in ("pieces", "category") if slots.get(key))
        return f"{detail} {'接单评估' if chinese else 'feasibility'}".strip()
    return ""


def _reply_labels(reply: AgentReply, chinese: bool) -> list[str]:
    metadata = reply.metadata
    labels = []
    records = metadata.get("tasks", {})
    for task_id in metadata.get("task_order", []):
        record = records.get(task_id, {})
        decision = record.get("decision") or record.get("current_decision") or {}
        label = _task_label(str(record.get("intent", "")), decision.get("slots", {}), chinese)
        if label and label not in labels:
            labels.append(label)
    if labels:
        return labels
    data = metadata.get("structured_result")
    label = _task_label(
        str(metadata.get("intent", "")), data if isinstance(data, Mapping) else {}, chinese
    )
    return [label] if label else []


def conversation_title(messages: Sequence[Mapping[str, Any]]) -> str:
    """Summarize the first user request and its first available assistant reply.

    Later follow-ups never rename an existing conversation. Existing sessions
    with previously truncated titles can be migrated from their intact messages.
    """
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    if first_user is None:
        return "New conversation"
    request = clean_request_text(str(first_user.get("content", "")))
    chinese = bool(re.search(r"[\u3400-\u9fff]", request))
    # Guided feasibility results contain capacity estimates, not request slots.
    # Recover the explicit quantity/category from the stored guided request;
    # keep its date in the unabridged hover text rather than the short label.
    guided = re.fullmatch(
        r"Feasibility\s*·\s*(\d[\d,]*)\s+(.+?)\s+by\s+\d{4}-\d{2}-\d{2}",
        request,
        flags=re.IGNORECASE,
    )
    if guided:
        return compact_title(
            _task_label(
                "order_feasibility",
                {"pieces": guided[1], "category": guided[2]},
                chinese,
            )
        )
    first_reply = next((m.get("reply") for m in messages if m.get("role") == "assistant"), None)
    if isinstance(first_reply, AgentReply):
        labels = _reply_labels(first_reply, chinese)
        if labels:
            return compact_title(" + ".join(labels))
    # A structured request already has a short, meaningful business label.
    # Other requests retain their own words rather than an invented summary.
    request = re.sub(r"^(?:please\s+)?(?:could|would|can) you\s+", "", request, flags=re.I)
    request = re.sub(r"^please\s+", "", request, flags=re.I)
    return compact_title(request) or "New conversation"
