from datetime import datetime, timedelta, timezone

import pytest

from app.accounts.session import (
    AuthorizationDenied,
    InMemoryAuthorizationRepository,
    InMemorySessionRepository,
    InvalidSession,
    SessionRepositoryError,
    SessionService,
)


class FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class TokenFactory:
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self) -> str:
        self.counter += 1
        return f"fixed-test-token-value-that-is-long-enough-{self.counter:08d}"


def default_authorization() -> InMemoryAuthorizationRepository:
    authorization = InMemoryAuthorizationRepository()
    for user_id, workspace_id in {
        ("user-1", "ws-1"),
        ("user-1", "ws-2"),
        ("user-2", "ws-1"),
        ("trusted-user", "workspace-a"),
    }:
        authorization.grant_workspace(user_id, workspace_id)
    return authorization


def service(clock: FixedClock | None = None, repo: InMemorySessionRepository | None = None):
    return SessionService(
        repo or InMemorySessionRepository(),
        default_authorization(),
        clock=clock or FixedClock(),
        token_factory=TokenFactory(),
        token_pepper=b"test-only-pepper-0123456789abcdef",
    )


def test_create_returns_secure_cookie_and_persists_only_token_digest() -> None:
    repo = InMemorySessionRepository()
    created = service(repo=repo).create_session(user_id="user-1", workspace_id="ws-1")

    assert created.session.user_id == "user-1"
    assert created.session.workspace_id == "ws-1"
    assert created.cookie.value == created.token
    assert created.cookie.http_only is True
    assert created.cookie.secure is True
    assert created.cookie.same_site == "Lax"
    assert created.cookie.path == "/"
    assert created.token not in repr(created.session)
    assert repo.get_by_token_hash(created.token) is None


def test_resolve_uses_token_as_identity_and_refreshes_sliding_expiry() -> None:
    clock = FixedClock()
    svc = service(clock)
    created = svc.create_session(user_id="trusted-user", workspace_id="workspace-a")
    clock.advance(hours=1)

    resolved = svc.resolve_session(created.token)

    assert resolved.user_id == "trusted-user"
    assert resolved.workspace_id == "workspace-a"
    assert resolved.last_seen_at == clock.current
    assert resolved.expires_at == clock.current + timedelta(days=14)


def test_repeated_session_checks_do_not_write_until_renewal_is_due() -> None:
    from unittest.mock import patch

    clock = FixedClock()
    repo = InMemorySessionRepository()
    svc = service(clock, repo)
    created = svc.create_session(user_id="trusted-user", workspace_id="workspace-a")

    with patch.object(repo, "touch", wraps=repo.touch) as touch:
        for _ in range(4):
            assert svc.resolve_session(created.token).session_id == created.session.session_id
        touch.assert_not_called()

        clock.advance(minutes=5)
        assert svc.resolve_session(created.token).last_seen_at == clock.current
        touch.assert_called_once()


def test_authorized_lookup_uses_one_repository_read_and_checks_revocation() -> None:
    from unittest.mock import patch

    from app.accounts.conversation.identity_adapter import SessionIdentityAdapter

    class AuthorizedRepo(InMemorySessionRepository):
        def get_authorized_by_token_hash(self, token_hash):
            return super().get_by_token_hash(token_hash)

    repo = AuthorizedRepo()
    authz = default_authorization()
    svc = SessionService(
        repo,
        authz,
        clock=FixedClock(),
        token_factory=TokenFactory(),
        token_pepper=b"test-only-pepper-0123456789abcdef",
    )
    created = svc.create_session(user_id="trusted-user", workspace_id="workspace-a")
    with patch.object(
        repo, "get_authorized_by_token_hash", wraps=repo.get_authorized_by_token_hash
    ) as read:
        with patch.object(authz, "can_access_workspace", side_effect=AssertionError("extra read")):
            principal = SessionIdentityAdapter(svc, authz).require_principal(created.token)
        assert principal.user_id == "trusted-user"
        read.assert_called_once()

    svc.revoke_session(created.token)
    with pytest.raises(InvalidSession):
        svc.resolve_authorized_current_user(created.token)


def test_unknown_token_and_client_chosen_id_are_rejected() -> None:
    svc = service()
    svc.create_session(user_id="user-1", workspace_id="ws-1")

    with pytest.raises(InvalidSession):
        svc.resolve_session("client-chosen-session-id")
    with pytest.raises(InvalidSession):
        svc.resolve_session(None)


def test_expired_session_is_rejected_at_exact_expiry() -> None:
    clock = FixedClock()
    svc = SessionService(
        InMemorySessionRepository(),
        default_authorization(),
        clock=clock,
        token_factory=TokenFactory(),
        token_pepper=b"test-only-pepper-0123456789abcdef",
        ttl=timedelta(seconds=5),
    )
    created = svc.create_session(user_id="user-1", workspace_id="ws-1")
    clock.advance(seconds=5)

    with pytest.raises(InvalidSession):
        svc.resolve_session(created.token)


def test_logout_revokes_token_and_is_idempotent() -> None:
    svc = service()
    created = svc.create_session(user_id="user-1", workspace_id="ws-1")

    assert svc.revoke_session(created.token) is True
    assert svc.revoke_session(created.token) is True
    with pytest.raises(InvalidSession):
        svc.resolve_session(created.token)
    assert svc.revoke_session("unknown-token") is False


def test_revoke_all_sessions_only_affects_matching_user() -> None:
    repo = InMemorySessionRepository()
    svc = service(repo=repo)
    user_session = svc.create_session(user_id="user-1", workspace_id="ws-1")
    other_session = svc.create_session(user_id="user-2", workspace_id="ws-1")

    assert svc.revoke_user_sessions("user-1") == 1
    with pytest.raises(InvalidSession):
        svc.resolve_session(user_session.token)
    assert svc.resolve_session(other_session.token).user_id == "user-2"


def test_session_list_and_revoke_are_scoped_to_user_and_workspace() -> None:
    svc = service()
    owned = svc.create_session(user_id="user-1", workspace_id="ws-1")
    same_user_other_workspace = svc.create_session(user_id="user-1", workspace_id="ws-2")
    other_user = svc.create_session(user_id="user-2", workspace_id="ws-1")

    assert svc.list_sessions(user_id="user-1", workspace_id="ws-1") == [owned.session]
    assert svc.revoke_session_by_id(
        session_id=owned.session.session_id, user_id="user-2", workspace_id="ws-1"
    ) is False
    assert svc.revoke_session_by_id(
        session_id=owned.session.session_id, user_id="user-1", workspace_id="ws-2"
    ) is False
    assert svc.revoke_session_by_id(
        session_id=owned.session.session_id, user_id="user-1", workspace_id="ws-1"
    ) is True
    assert svc.resolve_session(same_user_other_workspace.token).workspace_id == "ws-2"
    assert svc.resolve_session(other_user.token).user_id == "user-2"


def test_cookie_deletion_matches_security_attributes() -> None:
    cookie = service().cookie_deletion()

    assert cookie.value == ""
    assert cookie.max_age == 0
    assert cookie.http_only and cookie.secure
    assert cookie.same_site == "Lax"


@pytest.mark.parametrize("user_id,workspace_id", [("", "ws"), ("user", "")])
def test_creation_requires_authenticated_user_and_workspace(
    user_id: str, workspace_id: str
) -> None:
    with pytest.raises(ValueError):
        service().create_session(user_id=user_id, workspace_id=workspace_id)


def test_naive_clock_is_rejected() -> None:
    svc = SessionService(
        InMemorySessionRepository(),
        default_authorization(),
        clock=lambda: datetime(2026, 11, 8),
        token_factory=TokenFactory(),
        token_pepper=b"test-only-pepper-0123456789abcdef",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        svc.create_session(user_id="user-1", workspace_id="ws-1")


class BrokenRepository(InMemorySessionRepository):
    def create(self, session, token_hash):
        raise OSError("injected storage failure")


def test_repository_errors_are_wrapped_without_leaking_storage_details() -> None:
    svc = SessionService(
        BrokenRepository(),
        default_authorization(),
        clock=FixedClock(),
        token_factory=TokenFactory(),
        token_pepper=b"test-only-pepper-0123456789abcdef",
    )
    with pytest.raises(SessionRepositoryError, match="could not create session"):
        svc.create_session(user_id="user-1", workspace_id="ws-1")


def test_insecure_cookie_or_weak_pepper_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="Secure"):
        SessionService(
            InMemorySessionRepository(), InMemoryAuthorizationRepository(), cookie_secure=False
        )
    with pytest.raises(ValueError, match="32 bytes"):
        SessionService(
            InMemorySessionRepository(),
            InMemoryAuthorizationRepository(),
            token_pepper=b"short",
        )


def test_workspace_access_must_be_verified_server_side() -> None:
    svc = SessionService(InMemorySessionRepository(), InMemoryAuthorizationRepository())
    with pytest.raises(AuthorizationDenied):
        svc.create_session(user_id="user-1", workspace_id="client-picked-workspace")


def test_current_user_context_and_resource_ownership_are_server_derived() -> None:
    authz = InMemoryAuthorizationRepository()
    authz.grant_workspace("user-1", "ws-1")
    authz.grant_workspace("user-2", "ws-2")
    authz.add_thread("thread-a", "user-1", "ws-1")
    authz.add_thread("thread-b", "user-2", "ws-2")
    authz.add_message("message-a", "thread-a")
    authz.add_state("state-a", "thread-a")
    svc = SessionService(InMemorySessionRepository(), authz)
    created = svc.create_session(user_id="user-1", workspace_id="ws-1")

    context = svc.resolve_current_user(created.token)
    assert context.user_id == "user-1"
    assert context.workspace_id == "ws-1"
    assert svc.authorize_thread(created.token, "thread-a") is True
    assert svc.authorize_thread(created.token, "thread-b") is False
    assert svc.authorize_message(created.token, "message-a") is True
    assert svc.authorize_state(created.token, "state-a") is True
    assert svc.authorize_message(created.token, "unknown-message") is False
    with pytest.raises(InvalidSession):
        svc.authorize_thread("forged-token", "thread-a")
