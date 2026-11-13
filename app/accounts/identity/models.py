"""Stable identity types and safe public errors."""

from dataclasses import dataclass, field
from datetime import datetime


class AuthError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    username: str
    username_key: str
    password_hash: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Safe handoff to Person 2; contains neither password nor session token."""

    user_id: str
    username: str
