"""Register users and verify credentials without creating browser sessions."""

import re
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from .models import AuthenticatedUser, AuthError, UserRecord
from .passwords import Argon2PasswordHasher, PasswordHasher, validate_password
from .repository import DuplicateUsername, UserRepository
from .throttle import LoginThrottle

_USERNAME = re.compile(r"[a-z0-9_]{3,32}\Z", re.ASCII)


def normalize_username(raw: str) -> str:
    if not isinstance(raw, str):
        raise AuthError("INVALID_USERNAME")
    key = raw.strip().lower()
    if not _USERNAME.fullmatch(key):
        raise AuthError("INVALID_USERNAME")
    return key


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        throttle: LoginThrottle,
        *,
        hasher: PasswordHasher | None = None,
        clock: Callable[[], datetime] | None = None,
        user_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.users = users
        self.throttle = throttle
        self.hasher = hasher or Argon2PasswordHasher()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.user_id_factory = user_id_factory or (lambda: str(uuid4()))
        # Unknown accounts take the same password-verification path.
        self._dummy_hash = self.hasher.hash("account-not-present-dummy-password")

    def register(self, username: str, password: str) -> AuthenticatedUser:
        key = normalize_username(username)
        validate_password(password, key)
        try:
            password_hash = self.hasher.hash(password)
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("clock must return timezone-aware datetimes")
            user = UserRecord(
                user_id=self.user_id_factory(),
                username=key,
                username_key=key,
                password_hash=password_hash,
                created_at=now,
            )
            self.users.create(user)
        except DuplicateUsername:
            raise AuthError("USERNAME_TAKEN") from None
        except Exception:
            raise AuthError("STORAGE_ERROR") from None
        return AuthenticatedUser(user_id=user.user_id, username=user.username)

    def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        # Public login failures do not reveal whether the account exists.
        try:
            key = normalize_username(username)
        except AuthError:
            raise AuthError("INVALID_CREDENTIALS") from None
        try:
            valid_password_input = (
                isinstance(password, str) and len(password.encode("utf-8")) <= 1024
            )
        except UnicodeEncodeError:
            valid_password_input = False
        if not valid_password_input:
            raise AuthError("INVALID_CREDENTIALS")
        try:
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("clock must return timezone-aware datetimes")
            allowed = self.throttle.allow_attempt(key, now)
        except Exception:
            raise AuthError("STORAGE_ERROR") from None
        if not allowed:
            raise AuthError("RATE_LIMITED")
        try:
            user = self.users.get_by_username_key(key)
            matched = self.hasher.verify(user.password_hash if user else self._dummy_hash, password)
            if not user or not matched:
                raise AuthError("INVALID_CREDENTIALS")
            self.throttle.clear(key)
        except AuthError:
            raise
        except Exception:
            raise AuthError("STORAGE_ERROR") from None
        return AuthenticatedUser(user_id=user.user_id, username=user.username)
