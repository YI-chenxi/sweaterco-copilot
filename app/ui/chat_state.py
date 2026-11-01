"""Thread-local chat state and display adapters, independent of Streamlit."""

from copy import deepcopy
from typing import Any

from app.agent.contracts import AgentReply, Evidence
from app.agent.service import AgentService
from app.agent.turn_service import ChatSession, reset_request


def ensure_chat_session(thread: dict[str, Any]) -> ChatSession:
    if not isinstance(thread.get("agent_session"), ChatSession):
        thread["agent_session"] = ChatSession()
    return thread["agent_session"]


def run_chat_turn(thread: dict[str, Any], text: str, service: AgentService) -> AgentReply:
    """Commit the new session only after the complete turn returns successfully."""

    result = service.handle_turn(text, ensure_chat_session(thread))
    thread["agent_session"] = result.session
    return result.reply


def clear_chat_request(thread: dict[str, Any]) -> None:
    thread["agent_session"] = reset_request(ensure_chat_session(thread))


def task_replies(reply: AgentReply) -> list[tuple[str, AgentReply]]:
    """Read this turn's source replies only; never fabricate/replay old results."""

    parts = []
    for task_id in reply.metadata.get("task_order", []):
        record = reply.metadata.get("tasks", {}).get(task_id, {})
        raw = record.get("reply")
        if not isinstance(raw, dict):
            continue
        source = deepcopy(raw)
        source["evidence"] = [Evidence(**item) for item in source.get("evidence", [])]
        parts.append((task_id, AgentReply(**source)))
    return parts
