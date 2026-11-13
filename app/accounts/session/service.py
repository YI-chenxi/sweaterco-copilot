"""Secure opaque-token session lifecycle service."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.accounts.session.authorization import AuthorizationRepository
from app.accounts.session.models import (
    AuthorizationDenied,
    CreatedSession,
    CurrentUserContext,
    InvalidSession,
    Session,
    SessionCookie,
    SessionRepositoryError,
)
from app.accounts.session.repository import SessionRepository

Clock = Callable[[], datetime]


class SessionService:
    """Create and validate opaque sessions without trusting client-supplied IDs."""

    def __init__(
        self,
        repository: SessionRepository,
        authorization_repository: AuthorizationRepository,
        *,
        clock: Clock | None = None,
        token_factory: Callable[[], str] | None = None,
        token_pepper: bytes | None = None,
        ttl: timedelta = timedelta(days=14),
        sliding_ttl: bool = True,
        cookie_name: str = "sweaterco_session",
        cookie_secure: bool = True,
        cookie_same_site: str = "Lax",
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("ttl must be positive")
        if cookie_secure is not True:
            raise ValueError("session cookies must use Secure")
        if token_pepper is not None and len(token_pepper) < 32:
            raise ValueError("token_pepper must contain at least 32 bytes")
        if cookie_same_site not in {"Strict", "Lax"}:
            raise ValueError("cookie_same_site must be Strict or Lax")
        self._repository = repository
        self._authorization = authorization_repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._pepper = token_pepper if token_pepper is not None else secrets.token_bytes(32)
        self._ttl = ttl
        self._sliding_ttl = sliding_ttl
        # Resolving a session still reads its current revoked/expiry state on
        # every request. Renewing it on every check adds a database write and
        # network round trip to each Streamlit rerun.
        self._renew_after = min(timedelta(minutes=5), ttl / 4)
        self._cookie_name = cookie_name
        self._cookie_secure = cookie_secure
        self._cookie_same_site = cookie_same_site

    def create_session(self, *, user_id: str, workspace_id: str) -> CreatedSession:
        """Create after upstream authentication has established user identity."""
        if not user_id or not workspace_id:
            raise ValueError("user_id and workspace_id are required")
        if not self._authorization.can_access_workspace(user_id, workspace_id):
            raise AuthorizationDenied("user cannot access this workspace")
        now = self._now()
        token = self._token_factory()
        if not token or len(token) < 32:
            raise ValueError("token_factory must return an opaque token of at least 32 characters")
        expires_at = now + self._ttl
        session = Session(
            session_id=str(uuid4()),
            user_id=user_id,
            workspace_id=workspace_id,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
        )
        try:
            self._repository.create(session, self._token_hash(token))
        except Exception as exc:
            raise SessionRepositoryError("could not create session") from exc
        cookie = SessionCookie(
            name=self._cookie_name,
            value=token,
            max_age=int(self._ttl.total_seconds()),
            http_only=True,
            secure=self._cookie_secure,
            same_site=self._cookie_same_site,  # type: ignore[arg-type]
        )
        return CreatedSession(session=session, token=token, cookie=cookie)

    def resolve_session(self, token: str | None) -> Session:
        """Resolve the browser bearer token; any caller-provided user/session ID is ignored."""
        return self._resolve_session(token, self._repository.get_by_token_hash)

    def _resolve_session(
        self, token: str | None, lookup: Callable[[str], Session | None]
    ) -> Session:
        if not token:
            raise InvalidSession("session token is required")
        try:
            result = lookup(self._token_hash(token))
        except Exception as exc:
            raise SessionRepositoryError("could not load session") from exc
        if result is None:
            raise InvalidSession("session is invalid")
        session = result
        now = self._now()
        if session.revoked_at is not None or session.expires_at <= now:
            raise InvalidSession("session is expired or revoked")
        if self._sliding_ttl and now - session.last_seen_at >= self._renew_after:
            try:
                session = self._repository.touch(session.session_id, now, now + self._ttl)
            except Exception as exc:
                raise SessionRepositoryError("could not refresh session") from exc
            if session.revoked_at is not None or session.expires_at <= now:
                raise InvalidSession("session is expired or revoked")
        return session

    def list_sessions(self, *, user_id: str, workspace_id: str) -> list[Session]:
        """List sessions only within the caller's authorized user/workspace scope."""
        if not user_id or not workspace_id:
            raise ValueError("user_id and workspace_id are required")
        try:
            return self._repository.list_for_owner(user_id, workspace_id)
        except Exception as exc:
            raise SessionRepositoryError("could not list sessions") from exc

    def resolve_current_user(self, token: str | None) -> CurrentUserContext:
        """Build trusted authorization context only from a valid bearer token."""
        session = self.resolve_session(token)
        return CurrentUserContext(
            session_id=session.session_id,
            user_id=session.user_id,
            workspace_id=session.workspace_id,
        )

    def resolve_authorized_current_user(self, token: str | None) -> CurrentUserContext:
        """Resolve the session and membership together when storage supports it."""
        lookup = getattr(self._repository, "get_authorized_by_token_hash", None)
        if lookup is None:
            context = self.resolve_current_user(token)
            if not self._authorization.can_access_workspace(
                context.user_id, context.workspace_id
            ):
                raise AuthorizationDenied("user cannot access this workspace")
            return context
        session = self._resolve_session(token, lookup)
        return CurrentUserContext(session.session_id, session.user_id, session.workspace_id)

    def authorize_thread(self, token: str | None, thread_id: str) -> bool:
        """Check thread ownership using identity resolved from the bearer token."""
        if not thread_id:
            return False
        context = self.resolve_current_user(token)
        try:
            return self._authorization.get_thread_owner(thread_id) == (
                context.user_id,
                context.workspace_id,
            )
        except Exception as exc:
            raise SessionRepositoryError("could not authorize thread") from exc

    def authorize_message(self, token: str | None, message_id: str) -> bool:
        """Authorize a message through its server-owned parent thread."""
        if not message_id:
            return False
        try:
            thread_id = self._authorization.get_message_thread_id(message_id)
        except Exception as exc:
            raise SessionRepositoryError("could not authorize message") from exc
        if not thread_id:
            return False
        return self.authorize_thread(token, thread_id)

    def authorize_state(self, token: str | None, state_id: str) -> bool:
        """Authorize conversation state through its server-owned parent thread."""
        if not state_id:
            return False
        try:
            thread_id = self._authorization.get_state_thread_id(state_id)
        except Exception as exc:
            raise SessionRepositoryError("could not authorize state") from exc
        if not thread_id:
            return False
        return self.authorize_thread(token, thread_id)

    def revoke_session_by_id(
        self, *, session_id: str, user_id: str, workspace_id: str
    ) -> bool:
        """Revoke a session by ID only when it belongs to the authorized scope."""
        if not session_id or not user_id or not workspace_id:
            raise ValueError("session_id, user_id, and workspace_id are required")
        try:
            return self._repository.revoke_owned(
                session_id, user_id, workspace_id, self._now()
            ) is not None
        except Exception as exc:
            raise SessionRepositoryError("could not revoke session") from exc

    def revoke_session(self, token: str | None) -> bool:
        """Revoke the session matching the bearer token; repeated logout is harmless."""
        if not token:
            return False
        try:
            match = self._repository.get_by_token_hash(self._token_hash(token))
            if match is None:
                return False
            self._repository.revoke(match.session_id, self._now())
            return True
        except Exception as exc:
            raise SessionRepositoryError("could not revoke session") from exc

    def revoke_user_sessions(self, user_id: str) -> int:
        """Invalidate all sessions after password/security changes or account logout."""
        if not user_id:
            raise ValueError("user_id is required")
        try:
            return self._repository.revoke_user_sessions(user_id, self._now())
        except Exception as exc:
            raise SessionRepositoryError("could not revoke user sessions") from exc

    def cookie_deletion(self) -> SessionCookie:
        """Return matching secure cookie attributes for the UI to clear the cookie."""
        return SessionCookie(
            name=self._cookie_name,
            value="",
            max_age=0,
            http_only=True,
            secure=self._cookie_secure,
            same_site=self._cookie_same_site,  # type: ignore[arg-type]
        )

    def _token_hash(self, token: str) -> str:
        return hmac.new(self._pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)
