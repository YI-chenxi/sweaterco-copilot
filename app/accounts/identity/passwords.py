"""Password policy and library-backed Argon2id hashing."""

from typing import Protocol

from .models import AuthError


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


class Argon2PasswordHasher:
    def __init__(self) -> None:
        from argon2 import PasswordHasher as LibraryHasher
        from argon2.low_level import Type

        self._hasher = LibraryHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False
        except (InvalidHashError, VerificationError):
            raise AuthError("STORAGE_ERROR") from None


_BLOCKED = frozenset(
    {
        "passwordpassword",
        "password123456",
        "123456789012345",
        "1234567890123456",
        "qwertyuiopasdfgh",
        "letmeinletmein",
        "correcthorsebatterystaple",
    }
)


def validate_password(password: str, username_key: str) -> None:
    # Passwords are not stripped, lowercased, or otherwise transformed.
    if not isinstance(password, str) or not 15 <= len(password) <= 256:
        raise AuthError("INVALID_PASSWORD")
    try:
        encoded_length = len(password.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise AuthError("INVALID_PASSWORD") from exc
    if encoded_length > 1024 or "\x00" in password:
        raise AuthError("INVALID_PASSWORD")
    if password.casefold() in _BLOCKED or password.casefold() == username_key:
        raise AuthError("INVALID_PASSWORD")
