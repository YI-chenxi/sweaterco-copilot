
"""Deterministic conversation memory for business tasks."""

from copy import deepcopy
from dataclasses import replace

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    SlotUpdate,
    TaskState,
    TaskStatus,
)
from app.agent.intents import (
    Intent,
    IntentDecision,
    find_missing_slots,
)

ALLOWED_SLOTS = {
    Intent.ORDER_STATUS: frozenset({"order_id"}),
    Intent.PRODUCTION_NORMALITY: frozenset({"stage"}),
    Intent.MORNING_BRIEFING: frozenset({"top_n"}),
    Intent.ORDER_FEASIBILITY: frozenset(
        {"category", "pieces", "requested_delivery_date"}
    ),
    Intent.UNKNOWN: frozenset(),
}


def add_task(
    state: ConversationState,
    decision: IntentDecision,
    task_id: str,
    issues: list[InterpretationIssue] | None = None,
) -> ConversationState:
    """Add one independent task without mutating the previous state."""

    if not task_id.strip():
        raise ValueError("task_id cannot be empty")


    if any(task.task_id == task_id for task in state.tasks):
        raise ValueError(f"Duplicate task_id: {task_id}")

    allowed = ALLOWED_SLOTS[decision.intent]
    invalid = set(decision.slots) - allowed

    if invalid:
        raise ValueError(
            f"Unsupported slots for {decision.intent.value}: "
            f"{sorted(invalid)}"
        )


    decision_copy = replace(
    decision,
    slots=deepcopy(decision.slots),
    )

    missing = find_missing_slots(decision_copy)

    decision_copy = replace(
        decision_copy,
        missing_slots=missing,
    )

    task_issues = [
        replace(
            deepcopy(issue),
            decision_index=None,
            task_id=task_id,
        )
        for issue in (issues or [])
    ]

    status = TaskStatus.PENDING if missing or task_issues else TaskStatus.READY

    new_task = TaskState(
        task_id=task_id,
        decision=decision_copy,
        status=status,
        issues=task_issues,
    )

    new_state = deepcopy(state)
    new_state.tasks.append(new_task)

    return new_state


def apply_slot_update(
    state: ConversationState,
    update: SlotUpdate,
) -> ConversationState:
    """Update one existing task without mutating previous state."""

    if not update.task_id.strip():
        raise ValueError("task_id cannot be empty")

    new_state = deepcopy(state)

    target = next(
        (
            task
            for task in new_state.tasks
            if task.task_id == update.task_id
        ),
        None,
    )

    if target is None:
        raise ValueError(f"Unknown task_id: {update.task_id}")

    if target.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        raise ValueError(
            f"Cannot update task with status: {target.status.value}"
        )


    allowed = ALLOWED_SLOTS[target.decision.intent]
    invalid = set(update.slots) - allowed

    if invalid:
        raise ValueError(
            f"Unsupported slots for {target.decision.intent.value}: "
            f"{sorted(invalid)}"
        )

    new_slots = deepcopy(target.decision.slots)

    for key, value in update.slots.items():
        if value is None:
            new_slots.pop(key, None)
        else:
            new_slots[key] = deepcopy(value)


    updated_decision = replace(
        target.decision,
        slots=new_slots,
        missing_slots=(),
    )

    missing = find_missing_slots(updated_decision)

    updated_decision = replace(
        updated_decision,
        missing_slots=missing,
    )

    target.decision = updated_decision
    updated_fields = set(update.slots)
    target.issues = [
        issue
        for issue in target.issues
        if issue.field is None or issue.field not in updated_fields
    ]
    target.status = (
        TaskStatus.PENDING
        if missing or target.issues
        else TaskStatus.READY
    )

    return new_state



def cancel_task(
    state: ConversationState,
    task_id: str,
) -> ConversationState:
    """Remove one task without changing the previous state."""

    if not task_id.strip():
        raise ValueError("task_id cannot be empty")

    if not any(task.task_id == task_id for task in state.tasks):
        raise ValueError(f"Unknown task_id: {task_id}")

    new_state = deepcopy(state)
    new_state.tasks = [
        task for task in new_state.tasks
        if task.task_id != task_id
    ]

    return new_state



def retry_task(
    state: ConversationState,
    task_id: str,
) -> ConversationState:
    """Reopen a completed or failed task for another execution."""

    if not task_id.strip():
        raise ValueError("task_id cannot be empty")

    new_state = deepcopy(state)

    target = next(
        (
            task
            for task in new_state.tasks
            if task.task_id == task_id
        ),
        None,
    )

    if target is None:
        raise ValueError(f"Unknown task_id: {task_id}")

    if target.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        raise ValueError(
            f"Task is not finished: {target.status.value}"
        )

    updated_decision = replace(
        target.decision,
        missing_slots=(),
    )
    missing = find_missing_slots(updated_decision)

    target.decision = replace(
        updated_decision,
        missing_slots=missing,
    )
    target.status = (
        TaskStatus.PENDING
        if missing or target.issues
        else TaskStatus.READY
    )

    return new_state



def mark_task_result(
    state: ConversationState,
    task_id: str,
    success: bool,
) -> ConversationState:
    """Mark a READY task as completed or failed."""

    if not task_id.strip():
        raise ValueError("task_id cannot be empty")

    if not isinstance(success, bool):
        raise TypeError("success must be a bool")

    new_state = deepcopy(state)

    target = next(
        (
            task
            for task in new_state.tasks
            if task.task_id == task_id
        ),
        None,
    )

    if target is None:
        raise ValueError(f"Unknown task_id: {task_id}")

    if target.status != TaskStatus.READY:
        raise ValueError(
            f"Task is not ready: {target.status.value}"
        )

    target.status = (
        TaskStatus.COMPLETED if success else TaskStatus.FAILED
    )

    return new_state
