"""Storage-only data contracts for persistent conversations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ChatStorageError(Exception):
    """Safe storage error exposed to the conversation layer."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChatThread:
    thread_id: str
    user_id: str
    workspace_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    first_request: str | None = None
    preview_loaded: bool = False


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    thread_id: str
    sequence: int
    role: str
    content: str
    request_id: str | None
    reply_json: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True)
class AgentStateRecord:
    state_id: str
    thread_id: str
    schema_version: int
    state_json: dict[str, Any]
    revision: int
    updated_at: datetime


@dataclass(frozen=True)
class TurnRequest:
    thread_id: str
    request_id: str
    status: str
    request_fingerprint: str
    created_at: datetime
    updated_at: datetime
