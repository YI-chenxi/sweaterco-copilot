"""Execution coordination tests with mock handle() calls and real pure Memory."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock, call

import pytest

from app.agent.contracts import AgentReply, Evidence, ToolResult
from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    TaskState,
    TaskStatus,
)
from app.agent.enhancement.conversation import memory
from app.agent.enhancement.planning import planner
from app.agent.enhancement.planning.planner import (
    ExecutionOutcome,
    PlannedTask,
    PlanningResult,
    build_plan,
    classify_reply,
    execute_plan,
)
from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.response_builder import (
    build_clarification_reply,
    build_failure_reply,
    build_feasibility_reply,
    build_morning_briefing_reply,
)


def order_task(task_id="first", order_id="ORD-057", status=TaskStatus.READY):
    return TaskState(task_id, IntentDecision(Intent.ORDER_STATUS, {"order_id": order_id}), status)


def success_reply(intent=Intent.ORDER_STATUS):
    return AgentReply(
        text="Business result",
        evidence=[Evidence("orders.csv", "ORD-057", "Source row")],
        warnings=["Source warning"],
        assumptions=["Source assumption"],
        metadata={
            "intent": intent.value,
            "rule_version": "v1",
            "structured_result": {"judgement": "AT_RISK", "nested": [1, 2]},
            "custom": {"values": [3]},
        },
    )


def executor(*replies):
    mock = Mock(spec=["handle"])
    mock.handle.side_effect = list(replies)
    return mock


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (success_reply(), ExecutionOutcome.SUCCESS),
        (
            build_failure_reply(Intent.ORDER_STATUS, "invalid value"),
            ExecutionOutcome.TECHNICAL_FAILURE,
        ),
        (
            build_clarification_reply(IntentDecision(Intent.ORDER_STATUS), ("order_id",)),
            ExecutionOutcome.CLARIFICATION_REQUIRED,
        ),
        (AgentReply("No data", metadata={"status": "empty"}), ExecutionOutcome.NO_DATA),
        (AgentReply("Cannot accept this order"), ExecutionOutcome.UNCLASSIFIED),
        (
            AgentReply(
                "Unknown status", metadata={"status": "new_status", "structured_result": {}}
            ),
            ExecutionOutcome.UNCLASSIFIED,
        ),
        (
            AgentReply("No proof", metadata={"intent": "order_status", "structured_result": None}),
            ExecutionOutcome.UNCLASSIFIED,
        ),
        (
            AgentReply("Unknown business", metadata={"intent": "unknown", "structured_result": {}}),
            ExecutionOutcome.UNCLASSIFIED,
        ),
        (
            build_morning_briefing_reply(ToolResult(data={"findings": []})),
            ExecutionOutcome.SUCCESS,
        ),
    ],
)
def test_classification_uses_structured_signals_without_modifying_reply(reply, expected):
    before = deepcopy(reply)
    assert classify_reply(reply) == expected
    assert reply == before


def test_explicit_failure_takes_priority_over_other_reply_flags():
    reply = success_reply()
    reply.metadata["status"] = "failed"
    reply.needs_clarification = True
    assert classify_reply(reply) == ExecutionOutcome.TECHNICAL_FAILURE


def test_empty_result_is_not_success_even_with_structured_data():
    reply = success_reply()
    reply.metadata["status"] = "empty"
    assert classify_reply(reply) == ExecutionOutcome.NO_DATA


def test_two_successes_execute_in_planned_order_and_retain_both_memory_updates(monkeypatch):
    first, second = order_task(), order_task("second", "ORD-058")
    state = ConversationState(tasks=[first, second])
    planning = build_plan(state, ["second", "first"])
    handler = executor(success_reply(), success_reply())
    mark = Mock(wraps=memory.mark_task_result)
    monkeypatch.setattr(planner, "mark_task_result", mark)

    batch = execute_plan(state, planning, handler)

    assert handler.handle.call_args_list == [call(second.decision), call(first.decision)]
    assert [r.task_id for r in batch.results] == ["second", "first"]
    assert [r.decision for r in batch.results] == [second.decision, first.decision]
    assert all(r.outcome == ExecutionOutcome.SUCCESS and r.state_updated for r in batch.results)
    assert [task.status for task in batch.state.tasks] == [TaskStatus.COMPLETED] * 2
    assert [c.args[1] for c in mark.call_args_list] == ["second", "first"]
    assert mark.call_args_list[1].args[0].tasks[1].status == TaskStatus.COMPLETED
    assert batch.unresolved_task_ids == ()
    assert state.tasks[0].status == TaskStatus.READY


def test_negative_feasibility_result_is_completed_and_preserves_actual_builder_reply():
    decision = IntentDecision(
        Intent.ORDER_FEASIBILITY,
        {"category": "TOPS", "pieces": 800, "requested_delivery_date": "2026-04-25"},
    )
    state = ConversationState(tasks=[TaskState("new-order", decision, TaskStatus.READY)])
    reply = build_feasibility_reply(
        ToolResult(
            data={
                "estimate": "NOT_SUPPORTED",
                "working_days": 3,
                "capacity_margin": {"ASSEMBLY": -100},
                "assumptions": ["conservative estimate"],
                "external_options": [],
                "rule_version": "v1",
            },
            evidence=[Evidence("production_log.csv", "row-1", "Capacity evidence")],
            warnings=["Capacity shortage"],
        )
    )
    batch = execute_plan(state, build_plan(state, ["new-order"]), executor(reply))
    assert batch.results[0].outcome == ExecutionOutcome.SUCCESS
    assert batch.state.tasks[0].status == TaskStatus.COMPLETED
    assert batch.results[0].reply == reply
    assert batch.results[0].reply.metadata["structured_result"]["estimate"] == "NOT_SUPPORTED"


@pytest.mark.parametrize(
    "failure",
    [
        build_failure_reply(Intent.ORDER_STATUS, "validation failed"),
        RuntimeError("private diagnostic text"),
        TypeError("invalid input"),
        None,
    ],
)
def test_failure_does_not_prevent_next_independent_task(failure):
    state = ConversationState(tasks=[order_task(), order_task("second", "ORD-058")])
    handler = executor(failure, success_reply())
    batch = execute_plan(state, build_plan(state, ["first", "second"]), handler)

    assert handler.handle.call_count == 2
    assert [r.outcome for r in batch.results] == [
        ExecutionOutcome.TECHNICAL_FAILURE,
        ExecutionOutcome.SUCCESS,
    ]
    assert [task.status for task in batch.state.tasks] == [TaskStatus.FAILED, TaskStatus.COMPLETED]
    if isinstance(failure, Exception):
        assert batch.results[0].reply is None
        assert batch.results[0].error_type == type(failure).__name__
        assert str(failure) not in batch.results[0].message
    elif failure is None:
        assert batch.results[0].error_type == "TypeError"
    else:
        assert batch.results[0].reply == failure


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            build_clarification_reply(IntentDecision(Intent.ORDER_STATUS), ("order_id",)),
            ExecutionOutcome.CLARIFICATION_REQUIRED,
        ),
        (AgentReply("No data", metadata={"status": "empty"}), ExecutionOutcome.NO_DATA),
        (AgentReply("Unrecognized reply"), ExecutionOutcome.UNCLASSIFIED),
    ],
)
def test_unresolved_outcomes_are_recorded_without_memory_transition(reply, expected, monkeypatch):
    state = ConversationState(tasks=[order_task(), order_task("second", "ORD-058")])
    mark = Mock(wraps=memory.mark_task_result)
    monkeypatch.setattr(planner, "mark_task_result", mark)
    batch = execute_plan(
        state, build_plan(state, ["first", "second"]), executor(reply, success_reply())
    )

    assert batch.results[0].outcome == expected
    assert batch.results[0].reply == reply
    assert batch.results[0].state_updated is False
    assert batch.state.tasks[0].status == TaskStatus.READY
    assert batch.state.tasks[1].status == TaskStatus.COMPLETED
    assert batch.unresolved_task_ids == ("first",)
    assert "unchanged" in batch.results[0].message
    assert mark.call_count == 1
    assert mark.call_args.args[1] == "second"


def test_partial_plan_preserves_pending_notice_and_does_not_execute_pending_task():
    state = ConversationState(tasks=[order_task(), order_task("second", status=TaskStatus.PENDING)])
    handler = executor(success_reply())
    planning = build_plan(state, ["first", "second"])
    batch = execute_plan(state, planning, handler)
    assert handler.handle.call_count == 1
    assert [r.task_id for r in batch.results] == ["first"]
    assert batch.notices == planning.notices
    assert batch.state.tasks[1].status == TaskStatus.PENDING


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.COMPLETED, TaskStatus.FAILED])
def test_stale_status_is_skipped_without_execution_or_retry(status):
    state = ConversationState(tasks=[order_task(), order_task("second", "ORD-058")])
    planning = build_plan(state, ["first", "second"])
    state.tasks[0].status = status
    handler = executor(success_reply())
    batch = execute_plan(state, planning, handler)
    assert handler.handle.call_args_list == [call(state.tasks[1].decision)]
    assert batch.results[0].outcome == ExecutionOutcome.SKIPPED
    assert batch.results[0].reply is None
    assert batch.results[0].state_updated is False
    assert batch.state.tasks[0].status == status
    assert any(n.task_id == "first" and n.code == status.value for n in batch.notices)


@pytest.mark.parametrize(
    "change", ["slots", "removed_slot", "intent", "language", "missing", "issues"]
)
def test_stale_parameters_or_issues_block_task_but_allow_independent_task(change):
    state = ConversationState(tasks=[order_task(), order_task("second", "ORD-058")])
    planning = build_plan(state, ["first", "second"])
    if change == "slots":
        state.tasks[0].decision.slots["order_id"] = "ORD-099"
    elif change == "removed_slot":
        state.tasks[0].decision.slots.clear()
    elif change == "intent":
        state.tasks[0].decision = IntentDecision(Intent.UNKNOWN)
    elif change == "language":
        state.tasks[0].decision = replace(
            state.tasks[0].decision, language=ResponseLanguage.CHINESE
        )
    elif change == "missing":
        state.tasks[0].decision = replace(state.tasks[0].decision, missing_slots=("order_id",))
    else:
        state.tasks[0].issues.append(InterpretationIssue(InterpretationIssueType.POSSIBLE_TYPO))
    handler = executor(success_reply())
    batch = execute_plan(state, planning, handler)
    assert handler.handle.call_args_list == [call(state.tasks[1].decision)]
    assert batch.results[0].outcome == ExecutionOutcome.SKIPPED
    assert batch.results[0].decision == planning.planned_tasks[0].decision
    assert batch.state.tasks[0].status == TaskStatus.READY
    assert batch.state.tasks[1].status == TaskStatus.COMPLETED
    assert batch.notices


def test_removed_task_reference_raises_before_any_other_task_executes():
    state = ConversationState(tasks=[order_task(), order_task("second", "ORD-058")])
    planning = build_plan(state, ["first", "second"])
    state.tasks.pop()
    handler = executor(success_reply())
    with pytest.raises(ValueError, match="Unknown task_id"):
        execute_plan(state, planning, handler)
    handler.handle.assert_not_called()


@pytest.mark.parametrize("malformation", ["duplicate_plan", "duplicate_state", "too_many"])
def test_malformed_plan_or_state_is_rejected_before_execution(malformation):
    task = order_task()
    state = ConversationState(tasks=[task])
    planning = build_plan(state, ["first"])
    if malformation == "duplicate_plan":
        planning = PlanningResult(planning.planned_tasks * 2)
    elif malformation == "duplicate_state":
        state.tasks.append(deepcopy(task))
    else:
        planning = PlanningResult(
            tuple(PlannedTask(str(i), deepcopy(task.decision)) for i in range(3))
        )
    handler = executor(success_reply())
    with pytest.raises(ValueError):
        execute_plan(state, planning, handler)
    handler.handle.assert_not_called()


def test_identical_decisions_keep_distinct_result_identities():
    state = ConversationState(tasks=[order_task(), order_task("second")])
    first_reply, second_reply = success_reply(), success_reply()
    first_reply.text = "First returned result"
    second_reply.text = "Second returned result"
    handler = executor(first_reply, second_reply)
    batch = execute_plan(state, build_plan(state, ["second", "first"]), handler)
    assert [(r.task_id, r.reply.text) for r in batch.results] == [
        ("second", "First returned result"),
        ("first", "Second returned result"),
    ]


def test_inputs_and_original_reply_remain_unchanged_even_if_handler_mutates_decision():
    state = ConversationState(tasks=[order_task()])
    planning = build_plan(state, ["first"])
    reply = success_reply()
    state_before, plan_before, reply_before = deepcopy(state), deepcopy(planning), deepcopy(reply)

    def mutate_argument(decision):
        decision.slots["order_id"] = "ORD-999"
        return reply

    handler = Mock(spec=["handle"])
    handler.handle.side_effect = mutate_argument
    batch = execute_plan(state, planning, handler)
    assert state == state_before
    assert planning == plan_before
    assert reply == reply_before
    assert batch.results[0].decision == plan_before.planned_tasks[0].decision
    assert batch.results[0].reply == reply_before
    assert batch.results[0].reply is not reply

    batch.results[0].reply.metadata["custom"]["values"].append(4)
    batch.results[0].reply.evidence.clear()
    batch.results[0].decision.slots["order_id"] = "ORD-888"
    batch.state.tasks[0].decision.slots["order_id"] = "ORD-777"
    assert state == state_before
    assert planning == plan_before
    assert reply == reply_before


def test_reexecuting_finished_batch_state_never_retries_tasks():
    state = ConversationState(tasks=[order_task(), order_task("second", "ORD-058")])
    planning = build_plan(state, ["first", "second"])
    handler = executor(success_reply(), build_failure_reply(Intent.ORDER_STATUS, "failure"))
    first_batch = execute_plan(state, planning, handler)
    second_batch = execute_plan(first_batch.state, planning, handler)
    assert handler.handle.call_count == 2
    assert all(r.outcome == ExecutionOutcome.SKIPPED for r in second_batch.results)
    assert second_batch.state == first_batch.state


def test_empty_plan_calls_nothing_and_returns_detached_state():
    state = ConversationState(tasks=[order_task()])
    handler = executor()
    batch = execute_plan(state, build_plan(state, []), handler)
    handler.handle.assert_not_called()
    assert batch.results == ()
    assert batch.state == state
    assert batch.state is not state


def test_memory_errors_are_not_swallowed_or_misclassified_as_tool_failure(monkeypatch):
    state = ConversationState(tasks=[order_task()])
    monkeypatch.setattr(planner, "mark_task_result", Mock(side_effect=ValueError("Memory error")))
    with pytest.raises(ValueError, match="Memory error"):
        execute_plan(state, build_plan(state, ["first"]), executor(success_reply()))
