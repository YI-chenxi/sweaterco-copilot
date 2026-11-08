"""Username/password registration and credential verification."""

from .models import AuthenticatedUser, AuthError, UserRecord
from .repository import InMemoryUserRepository, UserRepository
from .service import AuthService

__all__ = [
    "AuthenticatedUser",
    "AuthError",
    "AuthService",
    "InMemoryUserRepository",
    "UserRecord",
    "UserRepository",
]
