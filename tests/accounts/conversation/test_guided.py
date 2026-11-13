"""Guided results use the actual P4 transaction, not frontend-supplied answer text."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.accounts.conversation import ConversationError
from app.accounts.conversation.codec import encode_session
from app.accounts.conversation.guided import prepare_guided
from app.accounts.conversation.runtime import AccountBackend
from app.agent.enhancement.conversation.memory import add_task
from app.agent.intents import Intent, IntentDecision
from app.agent.orchestrator import AgentOrchestrator
from app.agent.turn_service import ChatSession
from tests.accounts.conversation.fakes import FakeAgent, FakeIdentity, FakeRepository, uid


@pytest.fixture
def guided():
    identity, repository = FakeIdentity(), FakeRepository()
    tool = Mock(wraps=AgentOrchestrator())
    backend = AccountBackend(
        None, None, identity, repository, FakeAgent(), guided_orchestrator=tool
    )
    thread = repository.create(identity.users["a"])
    session = ChatSession(active_task_ids=["task-001"], next_task_number=5)
    session.state = add_task(session.state, IntentDecision(Intent.ORDER_STATUS), "task-001")
    repository.threads[thread] = replace(
        repository.threads[thread],
        state_document=encode_session(session),
    )
    return backend, repository, identity, tool, thread


CASES = [
    ("Order Status", {"order_id": "ORD-057"}),
    ("Production Health", {"stage": "PACKING"}),
    ("Morning Briefing", {"top_n": 3}),
    (
        "Check Feasibility",
        {
            "category": "TOPS",
            "pieces": 800,
            "requested_delivery_date": "2026-04-25",
        },
    ),
]


@pytest.mark.parametrize("workflow,values", CASES)
def test_four_workflows_commit_and_reopen_with_evidence(guided, workflow, values):
    backend, repository, identity, tool, thread = guided
    request = uid()
    original_state = backend.open_thread("a", thread).session
    result = backend.handle_guided_turn("a", thread, workflow, values, "en", request)
    restored = backend.open_thread("a", thread)
    assert restored.revision == 1 and len(restored.messages) == 2
    assert restored.messages[0].content == prepare_guided(workflow, values, "en").title
    assert restored.messages[1].reply == result.reply
    assert result.reply.evidence
    assert result.reply.metadata["ui_workflow_request"]["values"] == values
    assert restored.session == original_state
    replay = backend.handle_guided_turn("a", thread, workflow, values, "en", request)
    assert replay.replayed and replay.reply == result.reply
    assert tool.handle.call_count == 1
    assert len(repository.load(identity.users["a"], thread).messages) == 2


@pytest.mark.parametrize("handle,code", [("b", "FORBIDDEN"), ("expired", "UNAUTHENTICATED")])
def test_denied_identity_never_runs_the_workflow(guided, handle, code):
    backend, _, _, tool, thread = guided
    with pytest.raises(ConversationError) as error:
        backend.handle_guided_turn(
            handle, thread, "Order Status", {"order_id": "ORD-057"}, "en", uid()
        )
    assert error.value.code == code
    tool.handle.assert_not_called()


def test_changed_parameters_cannot_reuse_a_completed_request(guided):
    backend, _, _, tool, thread = guided
    request = uid()
    backend.handle_guided_turn("a", thread, "Order Status", {"order_id": "ORD-057"}, "en", request)
    with pytest.raises(ConversationError) as error:
        backend.handle_guided_turn(
            "a", thread, "Order Status", {"order_id": "ORD-058"}, "en", request
        )
    assert error.value.code == "CONFLICT"
    assert tool.handle.call_count == 1


def test_existing_fresh_request_control_persists_the_reset_without_a_business_call(guided):
    backend, repository, _, tool, thread = guided
    session = ChatSession(active_task_ids=["task-001"], next_task_number=8)
    session.state = add_task(session.state, IntentDecision(Intent.ORDER_STATUS), "task-001")
    repository.threads[thread] = replace(
        repository.threads[thread],
        state_document=encode_session(session),
    )
    backend.handle_guided_turn("a", thread, "Start a fresh request", {}, "en", uid())
    restored = backend.open_thread("a", thread)
    assert restored.session.active_task_ids == []
    assert restored.session.state.tasks == []
    assert restored.session.next_task_number == 8
    assert restored.messages[-1].reply.metadata["ui_control"] == "reset_request"
    tool.handle.assert_not_called()


@pytest.mark.parametrize("failure", ["before_commit", "after_commit"])
def test_unknown_commit_never_reexecutes(guided, failure):
    backend, repository, _, tool, thread = guided
    repository.fail = failure
    request = uid()
    args = ("a", thread, "Order Status", {"order_id": "ORD-057"}, "en", request)
    with pytest.raises(ConversationError):
        backend.handle_guided_turn(*args)
    if failure == "after_commit":
        assert backend.handle_guided_turn(*args).replayed
    else:
        with pytest.raises(ConversationError) as error:
            backend.handle_guided_turn(*args)
        assert error.value.code == "RECONCILIATION_REQUIRED"
    assert tool.handle.call_count == 1


@pytest.mark.parametrize(
    "workflow,values,language",
    [
        ("Delete Chat", {}, "en"),
        ("Order Status", {"order_id": "ORD-057", "user_id": "other"}, "en"),
        ("Morning Briefing", {"top_n": True}, "en"),
        ("Morning Briefing", {"top_n": 3}, []),
        ("Production Health", {"stage": []}, "en"),
        (
            "Check Feasibility",
            {"category": "TOPS", "pieces": 800, "requested_delivery_date": "tomorrow"},
            "en",
        ),
    ],
)
def test_malformed_requests_are_rejected_before_claim(guided, workflow, values, language):
    backend, repository, _, tool, thread = guided
    with pytest.raises(ConversationError) as error:
        backend.handle_guided_turn("a", thread, workflow, values, language, uid())
    assert error.value.code == "INVALID_ARGUMENT"
    assert not repository.requests
    tool.handle.assert_not_called()
