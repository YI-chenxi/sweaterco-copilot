"""Atomic per-username login attempt budget; durable storage in production."""

import hashlib
import hmac
from datetime import datetime, timedelta
from threading import RLock
from typing import Protocol


class AttemptRepository(Protocol):
    def consume(self, key: str, now: datetime, window: timedelta) -> int: ...

    def clear(self, key: str) -> None: ...


class InMemoryAttemptRepository:
    def __init__(self) -> None:
        self._attempts: dict[str, tuple[datetime, int]] = {}
        self._lock = RLock()

    def consume(self, key: str, now: datetime, window: timedelta) -> int:
        with self._lock:
            start, count = self._attempts.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0
            count += 1
            self._attempts[key] = (start, count)
            return count

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


class LoginThrottle:
    def __init__(
        self,
        repository: AttemptRepository,
        *,
        pepper: bytes,
        limit: int = 10,
        window: timedelta = timedelta(minutes=15),
    ) -> None:
        if len(pepper) < 32 or limit <= 0 or window.total_seconds() <= 0:
            raise ValueError("invalid login throttle configuration")
        self.repository = repository
        self.pepper = pepper
        self.limit = limit
        self.window = window

    def key(self, username_key: str) -> str:
        return hmac.new(self.pepper, username_key.encode("utf-8"), hashlib.sha256).hexdigest()

    def allow_attempt(self, username_key: str, now: datetime) -> bool:
        return self.repository.consume(self.key(username_key), now, self.window) <= self.limit

    def clear(self, username_key: str) -> None:
        self.repository.clear(self.key(username_key))
