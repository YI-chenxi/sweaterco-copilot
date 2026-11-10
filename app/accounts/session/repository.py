"""Session persistence boundary and deterministic in-memory repository."""

from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Protocol

from app.accounts.session.models import Session


class SessionRepository(Protocol):
    """Atomic storage operations required by SessionService."""

    def create(self, session: Session, token_hash: str) -> None: ...

    def get_by_token_hash(self, token_hash: str) -> Session | None: ...

    def list_for_owner(self, user_id: str, workspace_id: str) -> list[Session]: ...

    def touch(self, session_id: str, last_seen_at: datetime, expires_at: datetime) -> Session: ...

    def revoke(self, session_id: str, revoked_at: datetime) -> Session | None: ...

    def revoke_owned(
        self, session_id: str, user_id: str, workspace_id: str, revoked_at: datetime
    ) -> Session | None: ...

    def revoke_user_sessions(self, user_id: str, revoked_at: datetime) -> int: ...


class InMemorySessionRepository:
    """RepositoryMock for tests and local integration; not durable storage."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._token_hashes: dict[str, str] = {}
        self._lock = RLock()

    def create(self, session: Session, token_hash: str) -> None:
        with self._lock:
            if session.session_id in self._sessions or token_hash in self._token_hashes:
                raise ValueError("duplicate session")
            self._sessions[session.session_id] = session
            self._token_hashes[token_hash] = session.session_id

    def get_by_token_hash(self, token_hash: str) -> Session | None:
        with self._lock:
            session_id = self._token_hashes.get(token_hash)
            return self._sessions.get(session_id) if session_id is not None else None

    def list_for_owner(self, user_id: str, workspace_id: str) -> list[Session]:
        with self._lock:
            return sorted(
                (s for s in self._sessions.values()
                 if s.user_id == user_id and s.workspace_id == workspace_id),
                key=lambda session: session.created_at,
                reverse=True,
            )

    def touch(self, session_id: str, last_seen_at: datetime, expires_at: datetime) -> Session:
        with self._lock:
            session = self._sessions[session_id]
            if session.revoked_at is not None or session.expires_at <= last_seen_at:
                return session
            updated = Session(
                session_id=session.session_id,
                user_id=session.user_id,
                workspace_id=session.workspace_id,
                created_at=session.created_at,
                expires_at=expires_at,
                last_seen_at=last_seen_at,
                revoked_at=session.revoked_at,
            )
            self._sessions[session_id] = updated
            return updated

    def revoke(self, session_id: str, revoked_at: datetime) -> Session | None:
        with self._lock:
            return self._revoke_unlocked(session_id, revoked_at)

    def revoke_owned(
        self, session_id: str, user_id: str, workspace_id: str, revoked_at: datetime
    ) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or (session.user_id, session.workspace_id) != (
                user_id,
                workspace_id,
            ):
                return None
            return self._revoke_unlocked(session_id, revoked_at)

    def revoke_user_sessions(self, user_id: str, revoked_at: datetime) -> int:
        with self._lock:
            count = 0
            for session_id, session in tuple(self._sessions.items()):
                if session.user_id == user_id and session.revoked_at is None:
                    self._revoke_unlocked(session_id, revoked_at)
                    count += 1
            return count

    def _revoke_unlocked(self, session_id: str, revoked_at: datetime) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.revoked_at is None:
            session = Session(
                session_id=session.session_id,
                user_id=session.user_id,
                workspace_id=session.workspace_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
                last_seen_at=session.last_seen_at,
                revoked_at=revoked_at,
            )
            self._sessions[session_id] = session
        return session
