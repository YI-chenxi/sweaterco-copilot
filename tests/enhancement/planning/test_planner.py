"""Focused tests of pure planning, identity preservation, and input isolation."""

from copy import deepcopy

import pytest

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    TaskPlan,
    TaskState,
    TaskStatus,
)
from app.agent.enhancement.planning.planner import build_plan
from app.agent.intents import Intent, IntentDecision, ResponseLanguage


def order_task(task_id="task-001", status=TaskStatus.READY):
    return TaskState(
        task_id=task_id,
        decision=IntentDecision(
            intent=Intent.ORDER_STATUS,
            slots={"order_id": "ORD-057"},
            language=ResponseLanguage.CHINESE,
        ),
        status=status,
    )


def test_two_ready_tasks_keep_requested_order_and_identity():
    first = order_task()
    second = TaskState(
        task_id="task-002",
        decision=IntentDecision(Intent.PRODUCTION_NORMALITY, {"stage": "ASSEMBLY"}),
        status=TaskStatus.READY,
    )
    result = build_plan(ConversationState(tasks=[first, second]), ["task-002", "task-001"])

    assert isinstance(result.plan, TaskPlan)
    assert result.task_ids == ("task-002", "task-001")
    assert result.plan.tasks == [second.decision, first.decision]
    assert result.planned_tasks[1].decision.language == ResponseLanguage.CHINESE
    assert result.notices == ()
    assert result.plan.requires_clarification is False


def test_ready_and_pending_allow_partial_plan_with_missing_reason():
    pending = TaskState(
        task_id="task-002",
        decision=IntentDecision(Intent.ORDER_FEASIBILITY, {"category": "TOPS", "pieces": 800}),
    )
    result = build_plan(ConversationState(tasks=[order_task(), pending]), ["task-001", "task-002"])

    assert result.task_ids == ("task-001",)
    assert len(result.plan.tasks) == 1
    assert result.plan.requires_clarification is True
    assert [notice.code for notice in result.notices] == ["pending", "missing_slots"]
    assert all(notice.task_id == "task-002" for notice in result.notices)
    assert result.notices[1].missing_slots == ("requested_delivery_date",)
    assert "requested_delivery_date" in result.notices[1].message


def test_complete_pending_task_is_not_promoted():
    result = build_plan(
        ConversationState(tasks=[order_task(status=TaskStatus.PENDING)]), ["task-001"]
    )
    assert result.task_ids == ()
    assert result.notices[0].code == "pending"
    assert result.plan.requires_clarification is True


@pytest.mark.parametrize("issue_type", list(InterpretationIssueType))
def test_ready_task_with_any_unresolved_issue_is_blocked(issue_type):
    task = order_task()
    task.issues = [InterpretationIssue(issue_type=issue_type, task_id=task.task_id)]
    result = build_plan(ConversationState(tasks=[task]), [task.task_id])

    assert result.plan.tasks == []
    assert result.plan.requires_clarification is True
    assert result.notices[0].code == "unresolved_issues"
    assert result.notices[0].issue_types == (issue_type.value,)
    assert issue_type.value in result.notices[0].message


@pytest.mark.parametrize("slots", [{}, {"order_id": None}, {"order_id": "  "}])
def test_inconsistent_ready_task_missing_required_slots_is_blocked(slots):
    task = TaskState("task-001", IntentDecision(Intent.ORDER_STATUS, slots), TaskStatus.READY)
    result = build_plan(ConversationState(tasks=[task]), [task.task_id])

    assert result.plan.tasks == []
    assert result.notices[0].code == "missing_slots"
    assert result.notices[0].missing_slots == ("order_id",)
    assert result.plan.requires_clarification is True


def test_declared_missing_slots_are_respected_even_when_value_exists():
    task = TaskState(
        "task-001",
        IntentDecision(Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ("order_id",)),
        TaskStatus.READY,
    )
    result = build_plan(ConversationState(tasks=[task]), [task.task_id])
    assert result.plan.tasks == []
    assert result.notices[0].missing_slots == ("order_id",)


def test_missing_slots_and_issues_are_both_reported():
    task = TaskState(
        "task-001",
        IntentDecision(Intent.ORDER_STATUS),
        TaskStatus.READY,
        [InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE)],
    )
    result = build_plan(ConversationState(tasks=[task]), [task.task_id])
    assert [notice.code for notice in result.notices] == ["missing_slots", "unresolved_issues"]
    assert result.task_ids == ()


def test_completed_and_failed_tasks_are_skipped_without_retry():
    state = ConversationState(
        tasks=[
            order_task("task-001", TaskStatus.COMPLETED),
            order_task("task-002", TaskStatus.FAILED),
        ]
    )
    result = build_plan(state, ["task-002", "task-001"])
    assert result.plan.tasks == []
    assert result.task_ids == ()
    assert [(n.task_id, n.code) for n in result.notices] == [
        ("task-002", "failed"),
        ("task-001", "completed"),
    ]
    assert all("retry" in n.message for n in result.notices)
    assert result.plan.requires_clarification is False


def test_unknown_intent_is_blocked_even_when_ready():
    task = TaskState("task-001", IntentDecision(Intent.UNKNOWN), TaskStatus.READY)
    result = build_plan(ConversationState(tasks=[task]), [task.task_id])
    assert result.plan.tasks == []
    assert result.notices[0].code == "unsupported_intent"
    assert result.plan.requires_clarification is True


def test_identical_decisions_are_not_merged_or_matched_by_equality():
    result = build_plan(
        ConversationState(tasks=[order_task("first"), order_task("second")]),
        ("second", "first"),
    )
    assert result.task_ids == ("second", "first")
    assert len(result.plan.tasks) == 2


@pytest.mark.parametrize(
    ("task_ids", "message"),
    [
        (["task-001", "task-001"], "Duplicate requested"),
        (["task-001", "missing"], "Unknown task_id"),
        (["task-001", "task-002", "task-003"], "At most two"),
        ([""], "non-empty strings"),
        (["  "], "non-empty strings"),
        ([None], "non-empty strings"),
        ("task-001", "not a string"),
    ],
)
def test_invalid_requests_raise_without_mutating_input(task_ids, message):
    state = ConversationState(tasks=[order_task()])
    before = deepcopy(state)
    with pytest.raises(ValueError, match=message):
        build_plan(state, task_ids)
    assert state == before


def test_duplicate_state_ids_raise_instead_of_selecting_an_arbitrary_task():
    state = ConversationState(tasks=[order_task(), order_task()])
    with pytest.raises(ValueError, match="Duplicate task ID in conversation state"):
        build_plan(state, ["task-001"])


def test_invalid_state_id_raises():
    with pytest.raises(ValueError, match="non-empty strings"):
        build_plan(ConversationState(tasks=[order_task("")]), [])


def test_invalid_status_is_blocked():
    task = order_task()
    task.status = "unexpected"
    result = build_plan(ConversationState(tasks=[task]), [task.task_id])
    assert result.plan.tasks == []
    assert result.notices[0].code == "invalid_status"
    assert result.plan.requires_clarification is True


def test_empty_request_does_not_execute_unrequested_ready_tasks():
    result = build_plan(ConversationState(tasks=[order_task()]), [])
    assert result.plan.tasks == []
    assert result.task_ids == ()
    assert result.notices == ()
    assert result.plan.requires_clarification is False


def test_single_task_and_parameterless_briefing_are_supported():
    task = TaskState("briefing", IntentDecision(Intent.MORNING_BRIEFING), TaskStatus.READY)
    result = build_plan(ConversationState(tasks=[task, order_task()]), ["briefing"])
    assert result.task_ids == ("briefing",)
    assert result.plan.tasks == [task.decision]
    assert result.notices == ()


def test_planning_and_result_mutation_do_not_modify_input_data():
    first = order_task()
    pending = order_task("task-002", TaskStatus.PENDING)
    pending.issues.append(
        InterpretationIssue(
            InterpretationIssueType.POSSIBLE_TYPO,
            field="order_id",
            original_value={"nested": ["ODR057"]},
        )
    )
    state = ConversationState(tasks=[first, pending], language=ResponseLanguage.CHINESE)
    requested = ["task-001", "task-002"]
    before = deepcopy(state)
    result = build_plan(state, requested)

    assert state == before
    assert requested == ["task-001", "task-002"]
    assert result.planned_tasks[0].decision is not first.decision
    result.planned_tasks[0].decision.slots["order_id"] = "ORD-999"
    assert state == before


def test_public_plan_is_a_detached_snapshot_and_cannot_break_id_alignment():
    result = build_plan(ConversationState(tasks=[order_task()]), ["task-001"])
    public_plan = result.plan
    public_plan.tasks[0].slots["order_id"] = "ORD-999"
    public_plan.tasks.clear()
    assert result.task_ids == ("task-001",)
    assert result.plan.tasks[0].slots["order_id"] == "ORD-057"
