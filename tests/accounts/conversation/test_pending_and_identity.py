from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.accounts.conversation.contracts import PendingTurn, StoredMessage, TurnClaim
from app.accounts.conversation.errors import ConversationError
from app.accounts.conversation.identity_adapter import SessionIdentityAdapter, domain_call
from app.accounts.conversation.runtime import build_account_backend, compose_account_backend
from app.accounts.conversation.service import PersistentConversationService

from .fakes import FakeAgent, FakeIdentity, FakeRepository, uid


def test_reserved_user_message_reopens_without_running_or_resetting_it():
    identity, repository, agent = FakeIdentity(), FakeRepository(), FakeAgent()
    thread, request = repository.create(identity.users["a"]), uid()
    repository.threads[thread] = replace(
        repository.threads[thread],
        messages=(StoredMessage(uid(), request, 1, "user", "Assess 800 TOPS"),),
        pending=PendingTurn(request, "processing"),
    )
    facade = PersistentConversationService(agent, repository, identity)
    opened = facade.open_thread("a", thread)
    assert opened.revision == 0 and opened.pending.request_id == request
    assert len(opened.messages) == 1 and opened.session.next_task_number == 1
    repository.begin_turn = lambda *args: TurnClaim("in_progress")
    with pytest.raises(ConversationError, match="already in progress"):
        facade.handle_turn("a", thread, "Assess 800 TOPS", request)
    assert agent.calls == 0


@pytest.mark.parametrize(
    "pending",
    [
        PendingTurn("wrong-id", "processing"),
        PendingTurn(uid(), "invented"),
        PendingTurn(uid(), "failed"),
    ],
)
def test_pending_metadata_must_match_the_reserved_tail(pending):
    identity, repository = FakeIdentity(), FakeRepository()
    thread = repository.create(identity.users["a"])
    repository.threads[thread] = replace(
        repository.threads[thread],
        messages=(StoredMessage(uid(), uid(), 1, "user", "x"),),
        pending=pending,
    )
    with pytest.raises(ConversationError):
        PersistentConversationService(FakeAgent(), repository, identity).open_thread("a", thread)


def test_p3_replay_without_historical_state_returns_reply_only():
    identity, repository, agent = FakeIdentity(), FakeRepository(), FakeAgent()
    thread, request = repository.create(identity.users["a"]), uid()
    facade = PersistentConversationService(agent, repository, identity)
    first = facade.handle_turn("a", thread, "first", request)
    facade.handle_turn("a", thread, "second", uid())
    receipt = repository.requests[(thread, request)]["receipt"]
    repository.begin_turn = lambda *args: TurnClaim(
        "completed", replace(receipt, state_document=None)
    )
    replay = facade.handle_turn("a", thread, "first", request)
    assert replay.reply == first.reply and replay.replayed
    assert facade.open_thread("a", thread).revision == 2
    assert agent.calls == 2


def test_missing_state_still_forbidden_on_a_new_commit():
    identity, repository, agent = FakeIdentity(), FakeRepository(), FakeAgent()
    thread = repository.create(identity.users["a"])
    original = repository.commit_turn

    def incomplete(*args, **kwargs):
        return replace(original(*args, **kwargs), state_document=None)

    repository.commit_turn = incomplete
    with pytest.raises(ConversationError) as error:
        PersistentConversationService(agent, repository, identity).handle_turn(
            "a",
            thread,
            "new",
            uid(),
        )
    assert error.value.code == "INVALID_STATE"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("INVALID_SESSION", "UNAUTHENTICATED"),
        ("UNAUTHORIZED", "FORBIDDEN"),
        ("INVALID_CREDENTIALS", "INVALID_CREDENTIALS"),
        ("STORAGE_ERROR", "STORAGE_ERROR"),
        ("USERNAME_TAKEN", "USERNAME_TAKEN"),
        ("RATE_LIMITED", "RATE_LIMITED"),
        ("UNEXPECTED_PROVIDER_CODE", "STORAGE_ERROR"),
    ],
)
def test_domain_errors_never_expose_provider_exception_details(code, expected):
    class PrivateError(Exception):
        pass

    def fail():
        error = PrivateError("postgresql://SECRET/password")
        error.code = code
        raise error

    with pytest.raises(ConversationError) as error:
        domain_call(fail)
    assert error.value.code == expected
    assert "SECRET" not in str(error.value)
    assert error.value.__suppress_context__


def test_identity_adapter_checks_workspace_after_resolving_token():
    calls = []
    sessions = SimpleNamespace(
        resolve_current_user=lambda token: (
            calls.append(token)
            or SimpleNamespace(user_id="alice", workspace_id="w", session_id="s")
        )
    )
    authorization = SimpleNamespace(can_access_workspace=lambda user, workspace: False)
    adapter = SessionIdentityAdapter(sessions, authorization)
    with pytest.raises(ConversationError) as error:
        adapter.require_principal("opaque-test-token")
    assert error.value.code == "FORBIDDEN" and calls == ["opaque-test-token"]


@pytest.mark.parametrize("handle", [None, {}, 12, "", "x" * 4097])
def test_identity_rejects_unusable_cookie_values_without_provider_calls(handle):
    adapter = SessionIdentityAdapter(None, None)
    with pytest.raises(ConversationError) as error:
        adapter.require_principal(handle)
    assert error.value.code == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "secrets",
    [
        {},
        {"DATABASE_URL": "not-postgres", "ACCOUNT_SECRET": "x" * 32},
        {"DATABASE_URL": "postgresql://SECRET/password"},
        {"DATABASE_URL": "postgresql://SECRET/password", "ACCOUNT_SECRET": "short"},
    ],
)
def test_missing_or_bad_secrets_fail_safely_without_connecting(secrets):
    with pytest.raises(ConversationError) as error:
        build_account_backend(agent=FakeAgent(), secrets=secrets)
    assert error.value.code == "CONFIGURATION_ERROR"
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("secret", [None, b"short", "x" * 32])
def test_composition_does_not_generate_ephemeral_login_keys(secret):
    with pytest.raises(ConversationError) as error:
        compose_account_backend(agent=FakeAgent(), connection_factory=None, account_secret=secret)
    assert error.value.code == "CONFIGURATION_ERROR"
