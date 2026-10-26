"""Tests for shared enhancement contracts."""

from app.agent.enhancement.contracts import (
    ConversationState,
    SlotUpdate,
    TaskPlan,
    TaskState,
    TaskStatus,
    UnderstandingResult,
)
from app.agent.intents import Intent, IntentDecision, ResponseLanguage


def test_understanding_result():
    decision = IntentDecision(
        intent=Intent.ORDER_STATUS,
        slots={"order_id": "ORD-057"},
    )
    result = UnderstandingResult(decisions=[decision])

    assert len(result.decisions) == 1
    assert result.decisions[0].intent == Intent.ORDER_STATUS


def test_conversation_state_defaults():
    state = ConversationState()

    assert state.tasks == []
    assert state.language == ResponseLanguage.ENGLISH


def test_task_plan():
    decision = IntentDecision(
        intent=Intent.MORNING_BRIEFING,
        slots={},
    )
    plan = TaskPlan(tasks=[decision])

    assert len(plan.tasks) == 1
    assert plan.requires_clarification is False


def test_conversation_states_are_independent():
    first = ConversationState()
    second = ConversationState()

    first.tasks.append(
        TaskState(
            task_id="task-001",
            decision=IntentDecision(
                intent=Intent.ORDER_FEASIBILITY,
                slots={"category": "TOPS", "pieces": 800},
            ),
            status=TaskStatus.PENDING,
        )
    )

    assert len(first.tasks) == 1
    assert second.tasks == []


def test_multiple_tasks_keep_separate_slots():
    state = ConversationState(
        tasks=[
            TaskState(
                task_id="task-001",
                decision=IntentDecision(
                    intent=Intent.ORDER_STATUS,
                    slots={"order_id": "ORD-057"},
                ),
                status=TaskStatus.READY,
            ),
            TaskState(
                task_id="task-002",
                decision=IntentDecision(
                    intent=Intent.ORDER_FEASIBILITY,
                    slots={"category": "TOPS", "pieces": 800},
                    missing_slots=("requested_delivery_date",),
                ),
                status=TaskStatus.PENDING,
            ),
        ]
    )

    assert state.tasks[0].decision.slots == {
        "order_id": "ORD-057"
    }
    assert state.tasks[1].decision.slots == {
        "category": "TOPS",
        "pieces": 800,
    }


def test_task_status_values():
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.READY.value == "ready"
    assert TaskStatus.COMPLETED.value == "completed"
    assert TaskStatus.FAILED.value == "failed"


def test_slot_update():
    update = SlotUpdate(
        task_id="task-002",
        slots={"requested_delivery_date": "2026-04-25"},
    )

    assert update.task_id == "task-002"
    assert update.slots == {
        "requested_delivery_date": "2026-04-25"
    }


def test_slot_updates_have_independent_defaults():
    first = SlotUpdate(task_id="task-001")
    second = SlotUpdate(task_id="task-002")

    first.slots["pieces"] = 800

    assert second.slots == {}
