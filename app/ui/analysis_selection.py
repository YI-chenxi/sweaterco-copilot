"""Thread-local UI identifiers and selection; never combine answer payloads."""

from typing import Any
from uuid import uuid4

from app.agent.contracts import AgentReply


def ensure_reply_ids(thread: dict[str, Any]) -> None:
    seen = set()
    for message in thread["messages"]:
        if message["role"] != "assistant":
            continue
        identity = message.get("ui_reply_id")
        if not isinstance(identity, str) or not identity or identity in seen:
            identity = uuid4().hex
            message["ui_reply_id"] = identity
        seen.add(identity)


def selected_answer(thread: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    answers = [
        m
        for m in thread["messages"]
        if m["role"] == "assistant" and isinstance(m.get("reply"), AgentReply)
    ]
    identity = thread.get("selected_reply_id")
    if identity is not None:
        for number, message in enumerate(answers, 1):
            if message.get("ui_reply_id") == identity:
                return message, number
        return None, None
    return (answers[-1], len(answers)) if answers else (None, None)
