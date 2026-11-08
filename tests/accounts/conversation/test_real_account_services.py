"""Real P1/P2 services with in-memory repositories; not a PostgreSQL/browser test."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("app.accounts.identity")
pytest.importorskip("app.accounts.session")
pytest.importorskip("app.accounts.storage")

from app.accounts.identity.repository import InMemoryUserRepository
from app.accounts.identity.service import AuthService
from app.accounts.identity.throttle import InMemoryAttemptRepository, LoginThrottle
from app.accounts.session.authorization import InMemoryAuthorizationRepository
from app.accounts.session.repository import InMemorySessionRepository
from app.accounts.session.service import SessionService

from app.accounts.conversation.errors import ConversationError
from app.accounts.conversation.identity_adapter import SessionIdentityAdapter
from app.accounts.conversation.runtime import (
    AccountBackend,
    build_account_backend,
    compose_account_backend,
)

from .fakes import FakeAgent, uid

PASSWORD = "valid test-only passphrase 2026!"


@pytest.fixture
def account():
    authorization = InMemoryAuthorizationRepository()
    session_repository = InMemorySessionRepository()
    clock = [datetime(2026, 11, 8, tzinfo=timezone.utc)]
    auth = AuthService(
        InMemoryUserRepository(),
        LoginThrottle(InMemoryAttemptRepository(), pepper=b"x" * 32),
    )
    sessions = SessionService(
        session_repository,
        authorization,
        clock=lambda: clock[0],
        token_pepper=b"y" * 32,
        sliding_ttl=False,
    )
    backend = AccountBackend(
        auth,
        sessions,
        SessionIdentityAdapter(sessions, authorization),
        None,
        FakeAgent(),
    )
    return backend, authorization, session_repository, clock


def test_real_p1_p2_register_verify_login_token_and_logout(account):
    backend, authorization, repository, _ = account
    registered = backend.register("Alice_1", PASSWORD)
    # This grant emulates P3's already-verified workspace trigger; no DB claim.
    authorization.grant_workspace(registered.user_id, registered.user_id)
    assert repository.list_for_owner(registered.user_id, registered.user_id) == []
    login = backend.login("ALICE_1", PASSWORD)
    token = login.cookie.value
    assert login.workspace_id == registered.user_id
    assert token not in repr(login)
    assert login.cookie.http_only and login.cookie.secure and login.cookie.same_site == "Lax"
    principal = backend.current_user(token)
    assert principal.user_id == registered.user_id and principal.session_id == login.session_id
    assert backend.logout(token).max_age == 0
    with pytest.raises(ConversationError) as error:
        backend.current_user(token)
    assert error.value.code == "UNAUTHENTICATED"


def test_wrong_password_never_mints_a_session(account):
    backend, authorization, repository, _ = account
    user = backend.register("alice", PASSWORD)
    authorization.grant_workspace(user.user_id, user.user_id)
    with pytest.raises(ConversationError) as error:
        backend.login("alice", "wrong test password")
    assert error.value.code == "INVALID_CREDENTIALS"
    assert repository.list_for_owner(user.user_id, user.user_id) == []


def test_login_uses_server_workspace_mapping_and_denies_missing_membership(account):
    backend, _, repository, _ = account
    user = backend.register("alice", PASSWORD)
    with pytest.raises(ConversationError) as error:
        backend.login("alice", PASSWORD)
    assert error.value.code == "FORBIDDEN"
    assert repository.list_for_owner(user.user_id, user.user_id) == []


def test_real_session_expiry_denies_further_backend_requests(account):
    backend, authorization, _, clock = account
    user = backend.register("alice", PASSWORD)
    authorization.grant_workspace(user.user_id, user.user_id)
    token = backend.login("alice", PASSWORD).cookie.value
    clock[0] += timedelta(days=15)
    with pytest.raises(ConversationError) as error:
        backend.current_user(token)
    assert error.value.code == "UNAUTHENTICATED"


def test_user_or_session_id_cannot_replace_bearer_token(account):
    backend, authorization, _, _ = account
    user = backend.register("alice", PASSWORD)
    authorization.grant_workspace(user.user_id, user.user_id)
    login = backend.login("alice", PASSWORD)
    for claimed in (user.user_id, login.session_id):
        with pytest.raises(ConversationError) as error:
            backend.current_user(claimed)
        assert error.value.code == "UNAUTHENTICATED"


def test_stable_config_restores_real_p2_token_resolution_across_service_instances():
    # Replace storage only with the actual P2 in-memory repository, shared for this test.
    def no_connection():
        raise AssertionError("No database calls in this test")

    first = compose_account_backend(
        agent=FakeAgent(),
        connection_factory=no_connection,
        account_secret=b"s" * 32,
    )
    second = compose_account_backend(
        agent=FakeAgent(),
        connection_factory=no_connection,
        account_secret=b"s" * 32,
    )
    changed = compose_account_backend(
        agent=FakeAgent(),
        connection_factory=no_connection,
        account_secret=b"z" * 32,
    )
    authorization, repository = InMemoryAuthorizationRepository(), InMemorySessionRepository()
    user = uid()
    authorization.grant_workspace(user, user)
    for backend in (first, second, changed):
        backend._sessions._repository = repository
        backend._sessions._authorization = authorization
        backend._identity.authorization = authorization
    token = first._sessions.create_session(user_id=user, workspace_id=user).token
    assert second.current_user(token).user_id == user
    with pytest.raises(ConversationError):
        changed.current_user(token)


def test_streamlit_secrets_mapping_builds_all_real_adapters_without_connecting(monkeypatch):
    from app.accounts.storage import database

    calls = []

    def factory(url):
        calls.append(url)

        def fail():
            raise AssertionError("Construction must not connect")

        return fail

    monkeypatch.setattr(database, "create_connection_factory", factory)
    backend = build_account_backend(
        agent=FakeAgent(),
        secrets={"DATABASE_URL": "postgresql://test-only/db", "ACCOUNT_SECRET": "a" * 64},
    )
    assert calls == ["postgresql://test-only/db"]
    assert backend.__class__ is AccountBackend
