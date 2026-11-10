"""Persistence contract consumed by P4's conversation service."""

from typing import Any, Protocol

from app.accounts.storage.models import (
    AgentStateRecord,
    ChatMessage,
    ChatThread,
    TurnRequest,
)


class ChatRepository(Protocol):
    def create_thread(
        self, user_id: str, workspace_id: str, title: str
    ) -> ChatThread: ...

    def list_threads(
        self, user_id: str, workspace_id: str
    ) -> list[ChatThread]: ...

    def rename_thread(
        self, user_id: str, workspace_id: str,
        thread_id: str, title: str
    ) -> ChatThread: ...

    def delete_thread(
        self, user_id: str, workspace_id: str, thread_id: str
    ) -> None: ...

    def list_messages(
        self, user_id: str, workspace_id: str, thread_id: str
    ) -> list[ChatMessage]: ...

    def get_state(
        self, user_id: str, workspace_id: str, thread_id: str
    ) -> AgentStateRecord | None: ...

    def begin_turn(
        self, user_id: str, workspace_id: str,
        thread_id: str, request_id: str, user_message: str
    ) -> TurnRequest: ...

    def complete_turn(
        self,
        user_id: str,
        workspace_id: str,
        thread_id: str,
        request_id: str,
        assistant_message: str,
        reply_json: dict[str, Any],
        state_json: dict[str, Any],
        schema_version: int,
        expected_revision: int,
    ) -> dict[str, Any]: ...
