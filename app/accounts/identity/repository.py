"""User storage contract and deterministic thread-safe fake."""

from threading import RLock
from typing import Protocol

from .models import UserRecord


class DuplicateUsername(Exception):
    pass


class UserRepository(Protocol):
    def create(self, user: UserRecord) -> None: ...

    def get_by_username_key(self, username_key: str) -> UserRecord | None: ...


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._lock = RLock()

    def create(self, user: UserRecord) -> None:
        with self._lock:
            if user.username_key in self._users:
                raise DuplicateUsername()
            self._users[user.username_key] = user

    def get_by_username_key(self, username_key: str) -> UserRecord | None:
        with self._lock:
            return self._users.get(username_key)
