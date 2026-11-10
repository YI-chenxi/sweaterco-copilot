"""Session domain types and service errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


class SessionError(Exception):
    """Base error for rejected session operations."""

    code = "SESSION_ERROR"


class InvalidSession(SessionError):
    """The presented session token is unknown, expired, or revoked."""

    code = "INVALID_SESSION"


class SessionRepositoryError(SessionError):
    """The session repository failed to complete an operation."""

    code = "STORAGE_ERROR"


class AuthorizationDenied(SessionError):
    """The authenticated user does not own the requested workspace/resource."""

    code = "UNAUTHORIZED"


@dataclass(frozen=True, slots=True)
class Session:
    """Authenticated server-side session metadata; never contains the bearer token."""

    session_id: str
    user_id: str
    workspace_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionCookie:
    """Cookie instructions for a UI adapter; the service does not set browser cookies."""

    name: str
    value: str
    max_age: int
    path: str = "/"
    http_only: bool = True
    secure: bool = True
    same_site: Literal["Strict", "Lax"] = "Lax"


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """One-time token plus safe metadata and UI cookie instructions."""

    session: Session
    token: str
    cookie: SessionCookie


@dataclass(frozen=True, slots=True)
class CurrentUserContext:
    """Trusted identity resolved from a verified server-side session token."""

    session_id: str
    user_id: str
    workspace_id: str
