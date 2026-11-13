"""Queued model outputs + actual local business tools. No network/API key."""

import json

import pytest

from app.accounts.conversation.codec import decode_reply, encode_reply
from app.accounts.conversation.service import PersistentConversationService
from app.agent.enhancement.contracts import TaskStatus
from tests.test_chat_integration import payload, service, task

from .fakes import FakeIdentity, FakeRepository, uid


def test_pending_clarification_survives_facade_recreation_and_json_storage():
    agent, _, executor = service(
        payload(task(order_id="ORD-057"), task("order_feasibility", category="TOPS", pieces=800)),
        payload(updates=[{"task_id": "task-002", "requested_delivery_date": "2026-04-25"}]),
    )
    identity, repository = FakeIdentity(), FakeRepository()
    thread = repository.create(identity.users["a"])
    first_facade = PersistentConversationService(agent, repository, identity)
    first = first_facade.handle_turn("a", thread, "Check ORD-057 and assess 800 TOPS", uid())
    assert first.reply.needs_clarification
    assert len(executor.calls) == 1
    snap = repository.threads[thread]
    # Explicit JSON roundtrip: no hidden Python object references in the stored document.
    from dataclasses import replace

    repository.threads[thread] = replace(
        snap,
        state_document=json.loads(json.dumps(snap.state_document)),
    )
    second_facade = PersistentConversationService(agent, repository, identity)
    recovered = second_facade.open_thread("a", thread)
    assert recovered.session.state.tasks[0].status == TaskStatus.COMPLETED
    assert recovered.session.state.tasks[1].status == TaskStatus.PENDING
    assert recovered.session.state.tasks[1].decision.slots["pieces"] == 800
    assert recovered.session.next_task_number == 3
    second = second_facade.handle_turn("a", thread, "Delivery date is 2026-04-25", uid())
    assert len(executor.calls) == 2
    assert executor.calls[-1].intent.value == "order_feasibility"
    assert all(
        t.status == TaskStatus.COMPLETED
        for t in second_facade.open_thread("a", thread).session.state.tasks
    )
    assert second.reply.evidence and second.reply.assumptions
    history = second_facade.open_thread("a", thread)
    assert len(history.messages) == 4
    assert history.messages[1].reply == first.reply
    assert history.messages[3].reply == second.reply


@pytest.mark.parametrize(
    "workflow_request",
    [
        task(order_id="ORD-057"),
        task("production_normality", stage="ASSEMBLY"),
        task("morning_briefing", top_n=3),
        task(
            "order_feasibility", category="TOPS", pieces=800, requested_delivery_date="2026-04-25"
        ),
    ],
)
def test_all_existing_workflow_replies_keep_complete_evidence_and_metadata(workflow_request):
    agent, _, executor = service(payload(workflow_request))
    result = agent.handle_turn("Realistic request")
    restored = decode_reply(json.dumps(encode_reply(result.reply)))
    assert restored == result.reply
    assert restored.metadata == result.reply.metadata
    assert restored.evidence == result.reply.evidence
    assert len(executor.calls) == 1
