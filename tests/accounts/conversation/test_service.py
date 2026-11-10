from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from app.accounts.conversation.codec import encode_session
from app.accounts.conversation.contracts import Principal, TurnClaim, TurnReceipt
from app.accounts.conversation.errors import ConversationError
from app.accounts.conversation.service import PersistentConversationService
from app.agent.contracts import AgentReply
from app.agent.turn_service import TurnResult

from .fakes import FakeAgent, FakeIdentity, FakeRepository, uid


@pytest.fixture
def setup():
    agent, identity, repository = FakeAgent(), FakeIdentity(), FakeRepository()
    thread = repository.create(identity.users["a"])
    facade = PersistentConversationService(agent, repository, identity)
    return facade, agent, identity, repository, thread


def expect(code, fn):
    with pytest.raises(ConversationError) as error:
        fn()
    assert error.value.code == code
    assert "SECRET" not in str(error.value)


def test_turn_is_saved_as_one_pair_and_reopens_detached(setup):
    facade, agent, identity, repository, thread = setup
    result = facade.handle_turn("a", thread, "Check ORD-057", uid())
    assert not result.replayed and result.committed_revision == 1
    # New facade proves it did not depend on mutable instance/session state.
    reopened = PersistentConversationService(agent, repository, identity).open_thread("a", thread)
    assert [m.role for m in reopened.messages] == ["user", "assistant"]
    assert reopened.messages[0].content == "Check ORD-057"
    assert reopened.messages[1].reply == result.reply
    assert reopened.session.next_task_number == 2
    reopened.session.next_task_number = 100
    reopened.messages[1].reply.metadata.clear()
    result.reply.evidence.clear()
    assert facade.open_thread("a", thread).session.next_task_number == 2
    assert facade.open_thread("a", thread).messages[1].reply.evidence
    assert facade.open_thread("a", thread).messages[1].reply.metadata
    assert agent.calls == 1


def test_same_request_replay_does_not_execute_or_roll_back_later_state(setup):
    facade, agent, _, _, thread = setup
    first_id = uid()
    first = facade.handle_turn("a", thread, "first", first_id)
    facade.handle_turn("a", thread, "second", uid())
    replay = facade.handle_turn("a", thread, "first", first_id)
    assert replay.replayed and replay.reply == first.reply
    assert replay.committed_revision == 1
    assert not hasattr(replay, "session")
    assert agent.calls == 2
    assert facade.open_thread("a", thread).revision == 2
    assert facade.open_thread("a", thread).session.next_task_number == 3
    expect("CONFLICT", lambda: facade.handle_turn("a", thread, "changed text", first_id))


def test_identity_and_storage_are_required_no_implicit_guest(setup):
    _, agent, identity, _, thread = setup
    missing_identity = PersistentConversationService(agent)
    expect("UNAUTHENTICATED", lambda: missing_identity.open_thread("a", thread))
    missing_store = PersistentConversationService(agent, identity=identity)
    expect("STORAGE_ERROR", lambda: missing_store.open_thread("a", thread))
    assert agent.calls == 0


@pytest.mark.parametrize(
    "handle,code",
    [
        ("b", "FORBIDDEN"),
        ("other", "FORBIDDEN"),
        ("expired", "UNAUTHENTICATED"),
    ],
)
def test_both_read_and_write_enforce_owner_workspace_and_login(setup, handle, code):
    facade, agent, _, _, thread = setup
    expect(code, lambda: facade.open_thread(handle, thread))
    expect(code, lambda: facade.handle_turn(handle, thread, "secret?", uid()))
    assert agent.calls == 0


def test_no_state_can_leak_between_threads_or_users(setup):
    facade, agent, identity, repository, thread = setup
    second = repository.create(identity.users["a"])
    bob = repository.create(identity.users["b"])
    facade.handle_turn("a", thread, "alice first", uid())
    assert facade.open_thread("a", second).session.next_task_number == 1
    assert facade.open_thread("b", bob).messages == ()
    facade.handle_turn("b", bob, "bob first", uid())
    assert "alice" not in facade.open_thread("b", bob).messages[-1].content
    assert "bob" not in facade.open_thread("a", thread).messages[-1].content
    assert agent.calls == 2


@pytest.mark.parametrize(
    "principal",
    [
        None,
        {"user_id": "alice"},
        Principal("", "factory", "s"),
        Principal("alice", "", "s"),
        Principal("alice", "factory", " "),
    ],
)
def test_malformed_trusted_provider_result_is_denied(setup, principal):
    facade, agent, identity, _, thread = setup
    identity.users["a"] = principal
    expect("UNAUTHENTICATED", lambda: facade.open_thread("a", thread))
    assert agent.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread", "local-ui-thread-1"),
        ("thread", 3),
        ("request", "abc"),
        ("text", ""),
        ("text", "  "),
        ("text", None),
        ("text", "x" * 32001),
    ],
)
def test_bad_inputs_do_not_reserve_or_execute(setup, field, value):
    facade, agent, _, repository, thread = setup
    values = {"thread": thread, "request": uid(), "text": "valid"}
    values[field] = value
    expect(
        "INVALID_ARGUMENT",
        lambda: facade.handle_turn(
            "a",
            values["thread"],
            values["text"],
            values["request"],
        ),
    )
    assert agent.calls == 0 and repository.requests == {}


def test_missing_or_deleted_thread_is_not_recreated(setup):
    facade, agent, _, repository, thread = setup
    expect("NOT_FOUND", lambda: facade.open_thread("a", uid()))
    del repository.threads[thread]
    expect("NOT_FOUND", lambda: facade.handle_turn("a", thread, "x", uid()))
    assert agent.calls == 0 and thread not in repository.threads


def test_injected_storage_record_must_still_match_identity(setup):
    facade, agent, identity, repository, thread = setup
    repository.load = lambda p, t: replace(
        repository.threads[t],
        owner_user_id=identity.users["b"].user_id,
    )
    expect("FORBIDDEN", lambda: facade.open_thread("a", thread))
    assert agent.calls == 0


@pytest.mark.parametrize(
    "corruption,code",
    [
        ("missing_state", "INVALID_STATE"),
        ("unknown_version", "UNSUPPORTED_SCHEMA"),
        ("state_counter", "INVALID_STATE"),
        ("message_text", "INVALID_STATE"),
        ("message_role", "INVALID_STATE"),
        ("message_sequence", "INVALID_STATE"),
        ("reply_version", "UNSUPPORTED_SCHEMA"),
        ("missing_message", "INVALID_STATE"),
        ("mismatched_request", "INVALID_STATE"),
        ("duplicate_message_id", "INVALID_STATE"),
    ],
)
def test_corrupt_history_never_resets_or_invokes_agent(setup, corruption, code):
    facade, agent, _, repository, thread = setup
    facade.handle_turn("a", thread, "original", uid())
    snap = repository.threads[thread]
    state = snap.state_document
    messages = list(snap.messages)
    if corruption == "missing_state":
        state = None
    elif corruption == "unknown_version":
        state["schema_version"] = 99
    elif corruption == "state_counter":
        state["payload"]["next_task_number"] = 0
    elif corruption == "message_text":
        messages[1] = replace(messages[1], content="unmatched")
    elif corruption == "message_role":
        messages[0] = replace(messages[0], role="assistant")
    elif corruption == "message_sequence":
        messages[1] = replace(messages[1], sequence=1)
    elif corruption == "reply_version":
        messages[1].reply_document["schema_version"] = 99
    elif corruption == "missing_message":
        messages.pop()
    elif corruption == "mismatched_request":
        messages[1] = replace(messages[1], request_id=uid())
    elif corruption == "duplicate_message_id":
        messages[1] = replace(messages[1], id=messages[0].id)
    repository.threads[thread] = replace(snap, messages=tuple(messages), state_document=state)
    expect(code, lambda: facade.open_thread("a", thread))
    expect(code, lambda: facade.handle_turn("a", thread, "next", uid()))
    assert agent.calls == 1


def test_expired_or_switched_identity_after_execution_prevents_save(setup):
    facade, agent, identity, repository, thread = setup
    request = uid()
    agent.hook = lambda session: identity.users.pop("a")
    expect("UNAUTHENTICATED", lambda: facade.handle_turn("a", thread, "check", request))
    assert repository.threads[thread].revision == 0
    identity.users["a"] = Principal("alice", "factory", "new-session")
    expect("RECONCILIATION_REQUIRED", lambda: facade.handle_turn("a", thread, "check", request))
    assert agent.calls == 1


def test_identity_switch_before_agent_execution_is_caught(setup):
    facade, agent, identity, repository, thread = setup
    # Calls: public authentication, pre-claim authentication, pre-execution authentication.
    identity.hook = lambda n: identity.users.update(a=identity.users["b"]) if n == 3 else None
    expect("IDENTITY_CHANGED", lambda: facade.handle_turn("a", thread, "check", uid()))
    assert agent.calls == 0 and repository.threads[thread].revision == 0


def test_agent_failure_is_sanitized_and_cannot_be_retried_implicitly(setup):
    facade, agent, _, repository, thread = setup

    def fail(session):
        raise RuntimeError("SECRET agent credentials")

    agent.hook = fail
    request = uid()
    expect("AGENT_ERROR", lambda: facade.handle_turn("a", thread, "check", request))
    expect("RECONCILIATION_REQUIRED", lambda: facade.handle_turn("a", thread, "check", request))
    expect("REQUEST_IN_PROGRESS", lambda: facade.handle_turn("a", thread, "check", uid()))
    assert agent.calls == 1 and repository.threads[thread].messages == ()
    assert repository.requests[(thread, request)]["error_code"] == "AGENT_ERROR"


def test_unserializable_agent_output_is_not_partially_saved(setup):
    facade, agent, _, repository, thread = setup

    def bad(text, session):
        agent.calls += 1
        return TurnResult(AgentReply("ok", metadata={"bad": object()}), session)

    agent.handle_turn = bad
    request = uid()
    expect("UNSUPPORTED_VALUE", lambda: facade.handle_turn("a", thread, "check", request))
    assert repository.threads[thread].messages == ()
    assert repository.threads[thread].revision == 0
    expect("RECONCILIATION_REQUIRED", lambda: facade.handle_turn("a", thread, "check", request))
    assert agent.calls == 1


@pytest.mark.parametrize(
    "point,code,calls",
    [
        ("load", "STORAGE_ERROR", 0),
        ("before_claim", "STORAGE_OUTCOME_UNKNOWN", 0),
        ("after_claim", "STORAGE_OUTCOME_UNKNOWN", 0),
        ("before_commit", "STORAGE_OUTCOME_UNKNOWN", 1),
        ("after_commit", "STORAGE_OUTCOME_UNKNOWN", 1),
    ],
)
def test_storage_failures_do_not_report_unacknowledged_success(setup, point, code, calls):
    facade, agent, _, repository, thread = setup
    repository.fail = point
    request = uid()
    expect(code, lambda: facade.handle_turn("a", thread, "check", request))
    assert agent.calls == calls
    if point == "after_commit":
        replay = facade.handle_turn("a", thread, "check", request)
        assert replay.replayed
        assert agent.calls == 1
        assert len(facade.open_thread("a", thread).messages) == 2
    elif point in ("after_claim", "before_commit"):
        expected = "REQUEST_IN_PROGRESS" if point == "after_claim" else "RECONCILIATION_REQUIRED"
        expect(expected, lambda: facade.handle_turn("a", thread, "check", request))
        assert agent.calls == calls
    else:
        facade.handle_turn("a", thread, "check", request)
        assert agent.calls == 1


def test_failed_uncertain_marker_still_does_not_release_claim(setup):
    facade, agent, _, repository, thread = setup
    repository.fail = "mark_uncertain"

    def fail(session):
        raise RuntimeError("SECRET")

    agent.hook = fail
    request = uid()
    expect("AGENT_ERROR", lambda: facade.handle_turn("a", thread, "check", request))
    expect("REQUEST_IN_PROGRESS", lambda: facade.handle_turn("a", thread, "check", request))
    expect("REQUEST_IN_PROGRESS", lambda: facade.handle_turn("a", thread, "check", uid()))
    assert agent.calls == 1


def test_concurrent_facades_share_reservation_and_do_not_double_execute(setup):
    facade, agent, identity, repository, thread = setup
    entered, release = Event(), Event()

    def wait(session):
        entered.set()
        assert release.wait(5)

    agent.hook = wait
    request = uid()
    other = PersistentConversationService(agent, repository, identity)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(facade.handle_turn, "a", thread, "check", request)
        try:
            assert entered.wait(5)
            expect("REQUEST_IN_PROGRESS", lambda: other.handle_turn("a", thread, "check", request))
            expect(
                "REQUEST_IN_PROGRESS", lambda: other.handle_turn("a", thread, "different", uid())
            )
        finally:
            release.set()
        assert not future.result(timeout=5).replayed
    assert other.handle_turn("a", thread, "check", request).replayed
    assert agent.calls == 1


def test_revision_conflict_does_not_invoke_agent(setup):
    facade, agent, _, repository, thread = setup
    begin = repository.begin_turn

    def conflict(*args):
        raise ConversationError("CONFLICT")

    repository.begin_turn = conflict
    expect("CONFLICT", lambda: facade.handle_turn("a", thread, "check", uid()))
    assert agent.calls == 0
    repository.begin_turn = begin


def test_deleted_during_execution_is_not_resurrected(setup):
    facade, agent, _, repository, thread = setup
    agent.hook = lambda session: repository.threads.pop(thread)
    expect("NOT_FOUND", lambda: facade.handle_turn("a", thread, "check", uid()))
    assert thread not in repository.threads and agent.calls == 1


def test_malicious_replay_receipt_cannot_disclose_another_users_reply(setup):
    facade, agent, _, repository, thread = setup

    def foreign(*args):
        return TurnClaim(
            "completed",
            TurnReceipt(
                thread,
                "bob",
                "factory",
                args[2],
                args[3],
                0,
                1,
                {"secret": "do not return"},
                {},
            ),
        )

    repository.begin_turn = foreign
    expect("FORBIDDEN", lambda: facade.handle_turn("a", thread, "check", uid()))
    assert agent.calls == 0


def test_broken_repository_receipt_is_not_reported_as_success(setup):
    facade, agent, _, repository, thread = setup
    commit = repository.commit_turn

    def altered(*args, **kwargs):
        receipt = commit(*args, **kwargs)
        document = encode_session(facade.open_thread("a", thread).session)
        document["payload"]["next_task_number"] += 10
        return replace(receipt, state_document=document)

    repository.commit_turn = altered
    request = uid()
    expect("INVALID_STATE", lambda: facade.handle_turn("a", thread, "check", request))
    # The real committed receipt survives an acknowledgement validation failure.
    assert facade.handle_turn("a", thread, "check", request).replayed
    assert agent.calls == 1
