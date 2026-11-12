from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.accounts.identity import AuthError, AuthService, InMemoryUserRepository
from app.accounts.identity.passwords import Argon2PasswordHasher
from app.accounts.identity.throttle import InMemoryAttemptRepository, LoginThrottle

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)
PASSWORD = "valid-pass12"


def service(users=None, attempts=None, *, clock=None):
    users = users or InMemoryUserRepository()
    attempts = attempts or InMemoryAttemptRepository()
    auth = AuthService(
        users,
        LoginThrottle(attempts, pepper=b"test-only-throttle-pepper-32-bytes!!"),
        clock=clock or (lambda: NOW),
    )
    return auth, users, attempts


def test_register_and_login_return_safe_identity_and_argon2id_hash():
    auth, users, _ = service()
    registered = auth.register("  ALICE_1  ", PASSWORD)
    stored = users.get_by_username_key("alice_1")
    assert registered.username == "alice_1"
    assert auth.authenticate("Alice_1", PASSWORD) == registered
    assert stored.password_hash.startswith("$argon2id$")
    assert PASSWORD not in stored.password_hash
    assert stored.password_hash not in repr(stored)
    assert not hasattr(registered, "password_hash")


def test_case_conflict_and_invalid_input():
    auth, _, _ = service()
    auth.register("Alice", PASSWORD)
    with pytest.raises(AuthError, match="USERNAME_TAKEN"):
        auth.register("  alice ", PASSWORD)
    for bad in ("álice", "ab", "a-b", " "):
        with pytest.raises(AuthError, match="INVALID_USERNAME"):
            auth.register(bad, PASSWORD)
    with pytest.raises(AuthError, match="INVALID_PASSWORD"):
        auth.register("other", "short")
    with pytest.raises(AuthError, match="INVALID_PASSWORD"):
        auth.register("other", "sixteen-chars-xx")
    with pytest.raises(AuthError, match="INVALID_PASSWORD"):
        auth.register("other", "password123456")
    with pytest.raises(AuthError, match="INVALID_PASSWORD"):
        auth.register("other", "\ud800" * 6)


@pytest.mark.parametrize("password", ["sixsix", "fifteen-chars15"])
def test_password_length_boundaries_are_accepted(password):
    auth, _, _ = service()
    registered = auth.register("alice", password)
    assert auth.authenticate("alice", password) == registered


def test_wrong_and_unknown_credentials_have_same_public_error():
    auth, _, _ = service()
    auth.register("alice", PASSWORD)
    for username, password in (("alice", "wrong long password"), ("unknown", PASSWORD)):
        with pytest.raises(AuthError, match="INVALID_CREDENTIALS"):
            auth.authenticate(username, password)


def test_concurrent_registration_only_one_wins():
    auth, users, _ = service()

    def register(_):
        try:
            return auth.register("Alice", PASSWORD)
        except AuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(register, range(8)))
    assert sum(result == "USERNAME_TAKEN" for result in results) == 7
    assert sum(not isinstance(result, str) for result in results) == 1
    assert users.get_by_username_key("alice") is not None


def test_rate_limit_survives_service_recreation_and_resets_after_window():
    attempts = InMemoryAttemptRepository()
    auth, users, _ = service(attempts=attempts)
    auth.register("alice", PASSWORD)
    for _ in range(10):
        with pytest.raises(AuthError, match="INVALID_CREDENTIALS"):
            auth.authenticate("alice", "wrong long password")
    next_auth, _, _ = service(users=users, attempts=attempts)
    with pytest.raises(AuthError, match="RATE_LIMITED"):
        next_auth.authenticate("alice", PASSWORD)
    later, _, _ = service(users=users, attempts=attempts, clock=lambda: NOW + timedelta(minutes=15))
    assert later.authenticate("alice", PASSWORD).username == "alice"


def test_storage_failure_never_reports_success():
    class BrokenUsers(InMemoryUserRepository):
        def create(self, user):
            raise OSError("database unavailable")

        def get_by_username_key(self, key):
            raise OSError("database unavailable")

    auth, _, _ = service(users=BrokenUsers())
    with pytest.raises(AuthError, match="STORAGE_ERROR"):
        auth.register("alice", PASSWORD)
    with pytest.raises(AuthError, match="STORAGE_ERROR"):
        auth.authenticate("alice", PASSWORD)


def test_throttle_storage_failure_fails_closed():
    class BrokenAttempts(InMemoryAttemptRepository):
        def consume(self, key, now, window):
            raise OSError("database unavailable")

    auth, _, _ = service(attempts=BrokenAttempts())
    with pytest.raises(AuthError, match="STORAGE_ERROR"):
        auth.authenticate("alice", PASSWORD)


def test_password_hasher_rejects_wrong_password():
    hasher = Argon2PasswordHasher()
    encoded = hasher.hash(PASSWORD)
    assert hasher.verify(encoded, PASSWORD)
    assert not hasher.verify(encoded, "another long password")
