
"""Tests for deterministic conversation memory."""

import pytest

from app.agent.enhancement.contracts import (
    ConversationState,
    SlotUpdate,
    TaskStatus,
)
from app.agent.enhancement.conversation.memory import (
    add_task,
    apply_slot_update,
    cancel_task,
    retry_task,
)
from app.agent.intents import Intent, IntentDecision


def test_add_ready_task():
    state = ConversationState()

    decision = IntentDecision(
        intent=Intent.ORDER_STATUS,
        slots={"order_id": "ORD-057"},
    )

    updated = add_task(state, decision, "task-001")

    assert len(updated.tasks) == 1
    assert updated.tasks[0].status == TaskStatus.READY
    assert updated.tasks[0].decision.slots["order_id"] == "ORD-057"
    assert state.tasks == []


def test_add_pending_task():
    state = ConversationState()

    decision = IntentDecision(
        intent=Intent.ORDER_FEASIBILITY,
        slots={"category": "TOPS", "pieces": 800},
    )

    updated = add_task(state, decision, "task-002")

    assert updated.tasks[0].status == TaskStatus.PENDING


def test_multiple_tasks_are_independent():
    state = ConversationState()

    state = add_task(
        state,
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    state = add_task(
        state,
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={"category": "TOPS", "pieces": 800},
        ),
        "task-002",
    )

    assert len(state.tasks) == 2
    assert state.tasks[0].decision.slots == {"order_id": "ORD-057"}
    assert state.tasks[1].decision.slots == {
        "category": "TOPS",
        "pieces": 800,
    }


def test_duplicate_task_id_rejected():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    with pytest.raises(ValueError, match="Duplicate task_id"):
        add_task(
            state,
            IntentDecision(
                intent=Intent.MORNING_BRIEFING,
            ),
            "task-001",
        )


def test_empty_task_id_rejected():
    with pytest.raises(ValueError, match="task_id cannot be empty"):
        add_task(
            ConversationState(),
            IntentDecision(intent=Intent.MORNING_BRIEFING),
            " ",
        )


def test_input_slots_are_not_mutated():
    slots = {"category": "TOPS", "pieces": 800}

    decision = IntentDecision(
        intent=Intent.ORDER_FEASIBILITY,
        slots=slots,
    )

    updated = add_task(
        ConversationState(),
        decision,
        "task-001",
    )

    slots["pieces"] = 1000

    assert updated.tasks[0].decision.slots["pieces"] == 800


def test_fill_missing_delivery_date():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={"category": "TOPS", "pieces": 800},
            missing_slots=("requested_delivery_date",),
        ),
        "task-002",
    )

    updated = apply_slot_update(
        state,
        SlotUpdate(
            task_id="task-002",
            slots={"requested_delivery_date": "2026-04-25"},
        ),
    )

    assert updated.tasks[0].status == TaskStatus.READY
    assert updated.tasks[0].decision.missing_slots == ()
    assert updated.tasks[0].decision.slots["pieces"] == 800
    assert state.tasks[0].status == TaskStatus.PENDING


def test_update_existing_quantity():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={
                "category": "TOPS",
                "pieces": 800,
                "requested_delivery_date": "2026-04-25",
            },
        ),
        "task-002",
    )

    updated = apply_slot_update(
        state,
        SlotUpdate(
            task_id="task-002",
            slots={"pieces": 1000},
        ),
    )

    assert updated.tasks[0].decision.slots["pieces"] == 1000
    assert state.tasks[0].decision.slots["pieces"] == 800


def test_update_does_not_affect_other_tasks():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    state = add_task(
        state,
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={"category": "TOPS", "pieces": 800},
        ),
        "task-002",
    )

    updated = apply_slot_update(
        state,
        SlotUpdate(
            task_id="task-002",
            slots={"requested_delivery_date": "2026-04-25"},
        ),
    )

    assert updated.tasks[0].decision.slots == {
        "order_id": "ORD-057"
    }
    assert updated.tasks[1].status == TaskStatus.READY


def test_unknown_task_id_rejected():
    with pytest.raises(ValueError, match="Unknown task_id"):
        apply_slot_update(
            ConversationState(),
            SlotUpdate(
                task_id="task-999",
                slots={"pieces": 800},
            ),
        )


@pytest.mark.parametrize(
    "status",
    [TaskStatus.COMPLETED, TaskStatus.FAILED],
)
def test_finished_task_cannot_be_updated(status):
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    state.tasks[0].status = status

    with pytest.raises(ValueError, match="Cannot update task"):
        apply_slot_update(
            state,
            SlotUpdate(
                task_id="task-001",
                slots={"order_id": "ORD-099"},
            ),
        )



def test_reject_unsupported_slot():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    with pytest.raises(ValueError, match="Unsupported slots"):
        apply_slot_update(
            state,
            SlotUpdate(
                task_id="task-001",
                slots={"pieces": 800},
            ),
        )

    assert state.tasks[0].decision.slots == {
        "order_id": "ORD-057"
    }


def test_clear_delivery_date_returns_to_pending():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={
                "category": "TOPS",
                "pieces": 800,
                "requested_delivery_date": "2026-04-25",
            },
        ),
        "task-002",
    )

    assert state.tasks[0].status == TaskStatus.READY

    updated = apply_slot_update(
        state,
        SlotUpdate(
            task_id="task-002",
            slots={"requested_delivery_date": None},
        ),
    )

    assert updated.tasks[0].status == TaskStatus.PENDING
    assert (
        "requested_delivery_date"
        in updated.tasks[0].decision.missing_slots
    )
    assert (
        "requested_delivery_date"
        not in updated.tasks[0].decision.slots
    )

    assert state.tasks[0].status == TaskStatus.READY



def test_add_task_rejects_unsupported_slot():
    state = ConversationState()

    with pytest.raises(ValueError, match="Unsupported slots"):
        add_task(
            state,
            IntentDecision(
                intent=Intent.ORDER_STATUS,
                slots={
                    "order_id": "ORD-057",
                    "pieces": 800,
                },
            ),
            "task-001",
        )

    assert state.tasks == []


def test_add_task_accepts_optional_top_n():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.MORNING_BRIEFING,
            slots={"top_n": 5},
        ),
        "task-001",
    )

    assert state.tasks[0].status == TaskStatus.READY
    assert state.tasks[0].decision.slots["top_n"] == 5



def test_cancel_only_target_task():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )
    state = add_task(
        state,
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={"category": "TOPS", "pieces": 800},
        ),
        "task-002",
    )

    updated = cancel_task(state, "task-002")

    assert len(updated.tasks) == 1
    assert updated.tasks[0].task_id == "task-001"
    assert len(state.tasks) == 2


def test_cancel_unknown_task_rejected():
    with pytest.raises(ValueError, match="Unknown task_id"):
        cancel_task(ConversationState(), "task-999")


def test_retry_completed_task():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )
    state.tasks[0].status = TaskStatus.COMPLETED

    updated = retry_task(state, "task-001")

    assert updated.tasks[0].status == TaskStatus.READY
    assert state.tasks[0].status == TaskStatus.COMPLETED


def test_retry_failed_task():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_FEASIBILITY,
            slots={"category": "TOPS", "pieces": 800},
        ),
        "task-002",
    )
    state.tasks[0].status = TaskStatus.FAILED

    updated = retry_task(state, "task-002")

    assert updated.tasks[0].status == TaskStatus.PENDING
    assert "requested_delivery_date" in (
        updated.tasks[0].decision.missing_slots
    )


def test_retry_unfinished_task_rejected():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    with pytest.raises(ValueError, match="Task is not finished"):
        retry_task(state, "task-001")



def test_separate_conversations_do_not_share_updates():
    first = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    second = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-099"},
        ),
        "task-001",
    )

    updated = apply_slot_update(
        first,
        SlotUpdate(
            task_id="task-001",
            slots={"order_id": "ORD-088"},
        ),
    )

    assert updated.tasks[0].decision.slots["order_id"] == "ORD-088"
    assert first.tasks[0].decision.slots["order_id"] == "ORD-057"
    assert second.tasks[0].decision.slots["order_id"] == "ORD-099"


def test_failed_update_preserves_original_state():
    state = add_task(
        ConversationState(),
        IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
        ),
        "task-001",
    )

    with pytest.raises(ValueError, match="Unsupported slots"):
        apply_slot_update(
            state,
            SlotUpdate(
                task_id="task-001",
                slots={"pieces": 800},
            ),
        )

    assert state.tasks[0].decision.slots == {
        "order_id": "ORD-057"
    }
    assert state.tasks[0].status == TaskStatus.READY


def test_retry_unknown_task_rejected():
    with pytest.raises(ValueError, match="Unknown task_id"):
        retry_task(ConversationState(), "task-999")
