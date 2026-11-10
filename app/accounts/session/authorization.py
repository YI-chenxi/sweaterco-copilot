"""Server-side workspace membership and chat-resource authorization boundary."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol


class AuthorizationRepository(Protocol):
    """Reads trusted workspace membership and parent-thread ownership."""

    def can_access_workspace(self, user_id: str, workspace_id: str) -> bool: ...

    def get_thread_owner(self, thread_id: str) -> tuple[str, str] | None: ...

    def get_message_thread_id(self, message_id: str) -> str | None: ...

    def get_state_thread_id(self, state_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    thread_id: str
    user_id: str
    workspace_id: str


class InMemoryAuthorizationRepository:
    """RepositoryMock for server-derived memberships and resource ownership."""

    def __init__(self) -> None:
        self._memberships: set[tuple[str, str]] = set()
        self._threads: dict[str, ThreadRecord] = {}
        self._messages: dict[str, str] = {}
        self._states: dict[str, str] = {}
        self._lock = RLock()

    def grant_workspace(self, user_id: str, workspace_id: str) -> None:
        with self._lock:
            self._memberships.add((user_id, workspace_id))

    def add_thread(self, thread_id: str, user_id: str, workspace_id: str) -> None:
        with self._lock:
            self._threads[thread_id] = ThreadRecord(thread_id, user_id, workspace_id)
            self._memberships.add((user_id, workspace_id))

    def add_message(self, message_id: str, thread_id: str) -> None:
        with self._lock:
            self._messages[message_id] = thread_id

    def add_state(self, state_id: str, thread_id: str) -> None:
        with self._lock:
            self._states[state_id] = thread_id

    def can_access_workspace(self, user_id: str, workspace_id: str) -> bool:
        with self._lock:
            return (user_id, workspace_id) in self._memberships

    def get_thread_owner(self, thread_id: str) -> tuple[str, str] | None:
        with self._lock:
            thread = self._threads.get(thread_id)
            return (thread.user_id, thread.workspace_id) if thread else None

    def get_message_thread_id(self, message_id: str) -> str | None:
        with self._lock:
            return self._messages.get(message_id)

    def get_state_thread_id(self, state_id: str) -> str | None:
        with self._lock:
            return self._states.get(state_id)
