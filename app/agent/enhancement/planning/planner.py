"""Task planning and explicit execution coordination, without reply aggregation.

References must identify at most two tasks explicitly. Invalid references raise
ValueError rather than producing a partial plan. Slot completeness uses the
existing project utility; semantic value validation belongs to Clarification.
"""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from app.agent.contracts import AgentReply
from app.agent.enhancement.contracts import ConversationState, TaskPlan, TaskState, TaskStatus
from app.agent.enhancement.conversation.memory import mark_task_result
from app.agent.intents import Intent, IntentDecision, find_missing_slots

if TYPE_CHECKING:
    from app.agent.orchestrator import AgentOrchestrator

_SUPPORTED_INTENTS = frozenset(
    {
        Intent.ORDER_STATUS,
        Intent.PRODUCTION_NORMALITY,
        Intent.MORNING_BRIEFING,
        Intent.ORDER_FEASIBILITY,
    }
)


@dataclass(frozen=True)
class PlannedTask:
    """One eligible task, with its original identity and an isolated decision."""

    task_id: str
    decision: IntentDecision


@dataclass(frozen=True)
class PlanningNotice:
    """Why a requested task is waiting, blocked, or skipped.

    Codes are pending, missing_slots, unresolved_issues, unsupported_intent,
    invalid_status, completed, or failed. Completed/failed notices describe
    skipped historical tasks; every other code requires clarification or repair.
    """

    task_id: str
    code: str
    message: str
    missing_slots: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningResult:
    """Keep identities attached to decisions without changing shared contracts.

    task_ids and plan are derived from the same ordered entries. Each plan
    access returns a fresh snapshot, so editing its mutable lists or slots does
    not alter the stored entries or the input ConversationState.
    """

    planned_tasks: tuple[PlannedTask, ...] = ()
    notices: tuple[PlanningNotice, ...] = ()

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.planned_tasks)

    @property
    def plan(self) -> TaskPlan:
        return TaskPlan(
            tasks=[deepcopy(task.decision) for task in self.planned_tasks],
            requires_clarification=any(
                notice.code not in {"completed", "failed"} for notice in self.notices
            ),
        )


def _validate_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("Task IDs must be non-empty strings")


def _task_notices(task: TaskState) -> list[PlanningNotice]:
    """Report blockers without promoting, repairing, or reopening a task."""

    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        code = "completed" if task.status == TaskStatus.COMPLETED else "failed"
        return [
            PlanningNotice(
                task.task_id,
                code,
                f"Task {task.task_id} is already {code}; skipped. "
                "An explicit retry is required before another execution.",
            )
        ]

    if task.status not in {TaskStatus.READY, TaskStatus.PENDING}:
        return [
            PlanningNotice(
                task.task_id,
                "invalid_status",
                f"Task {task.task_id} has an unsupported status; blocked.",
            )
        ]

    notices = []
    if task.status == TaskStatus.PENDING:
        notices.append(
            PlanningNotice(
                task.task_id,
                "pending",
                f"Task {task.task_id} is PENDING; waiting for upstream validation "
                "or clarification. The planner does not promote tasks to READY.",
            )
        )

    if task.decision.intent not in _SUPPORTED_INTENTS:
        notices.append(
            PlanningNotice(
                task.task_id,
                "unsupported_intent",
                f"Task {task.task_id} has an unsupported intent; "
                "a supported business intent must be identified first.",
            )
        )
    else:
        missing = find_missing_slots(task.decision)
        if missing:
            notices.append(
                PlanningNotice(
                    task.task_id,
                    "missing_slots",
                    f"Task {task.task_id} is missing required information: "
                    f"{', '.join(missing)}; waiting for clarification.",
                    missing_slots=missing,
                )
            )

    if task.issues:
        issue_types = tuple(issue.issue_type.value for issue in task.issues)
        notices.append(
            PlanningNotice(
                task.task_id,
                "unresolved_issues",
                f"Task {task.task_id} has unresolved interpretation issues: "
                f"{', '.join(issue_types)}; waiting for clarification.",
                issue_types=issue_types,
            )
        )
    return notices


def build_plan(state: ConversationState, task_ids: Sequence[str]) -> PlanningResult:
    """Plan zero, one, or two explicitly requested tasks in request order.

    Only READY, supported, complete, issue-free tasks are selected. PENDING
    tasks wait even if their slots appear complete; finished tasks are skipped.
    Invalid IDs, duplicate IDs in either the request or state, missing targets,
    and requests exceeding two tasks raise ValueError before any planning.

    Neither the state nor any input decision is mutated. No tool, model,
    execution coordinator, or Memory mutation function is called.
    """

    if isinstance(task_ids, (str, bytes)):
        raise ValueError("task_ids must be a sequence of task IDs, not a string")
    requested = tuple(task_ids)
    if len(requested) > 2:
        raise ValueError("At most two tasks may be requested")
    for task_id in requested:
        _validate_id(task_id)
    if len(set(requested)) != len(requested):
        raise ValueError("Duplicate requested task IDs")

    tasks_by_id = {}
    for task in state.tasks:
        _validate_id(task.task_id)
        if task.task_id in tasks_by_id:
            raise ValueError(f"Duplicate task ID in conversation state: {task.task_id}")
        tasks_by_id[task.task_id] = task
    for task_id in requested:
        if task_id not in tasks_by_id:
            raise ValueError(f"Unknown task_id: {task_id}")

    planned_tasks = []
    notices = []
    for task_id in requested:
        task = tasks_by_id[task_id]
        task_notices = _task_notices(task)
        notices.extend(task_notices)
        if not task_notices:
            planned_tasks.append(PlannedTask(task_id, deepcopy(task.decision)))

    return PlanningResult(tuple(planned_tasks), tuple(notices))


class ExecutionOutcome(str, Enum):
    """Execution outcome, separate from the business judgement and Memory status."""

    SUCCESS = "success"
    TECHNICAL_FAILURE = "technical_failure"
    CLARIFICATION_REQUIRED = "clarification_required"
    NO_DATA = "no_data"
    UNCLASSIFIED = "unclassified"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TaskExecutionResult:
    """A decision snapshot and its reply, bound to the original task identity.

    reply is a complete detached copy of the returned AgentReply, or None when
    execution was skipped or raised an exception. Exception text is deliberately
    not copied into public records; error_type identifies the exception class.
    """

    task_id: str
    decision: IntentDecision
    outcome: ExecutionOutcome
    reply: AgentReply | None = None
    message: str | None = None
    error_type: str | None = None
    state_updated: bool = False


@dataclass(frozen=True)
class ExecutionBatch:
    """New Memory state, ordered execution records, and planning/stale notices."""

    state: ConversationState
    results: tuple[TaskExecutionResult, ...] = ()
    notices: tuple[PlanningNotice, ...] = ()

    @property
    def unresolved_task_ids(self) -> tuple[str, ...]:
        """Attempted tasks whose Memory transition requires integration policy."""

        return tuple(
            result.task_id
            for result in self.results
            if result.outcome
            in {
                ExecutionOutcome.CLARIFICATION_REQUIRED,
                ExecutionOutcome.NO_DATA,
                ExecutionOutcome.UNCLASSIFIED,
            }
        )


def classify_reply(reply: AgentReply) -> ExecutionOutcome:
    """Classify existing structured reply signals without interpreting prose.

    Explicit failure takes precedence, followed by clarification and empty data.
    Existing normal builders include a dictionary structured_result and a known
    business intent, with no status flag. Negative judgements remain successes.
    Missing or unfamiliar signals stay UNCLASSIFIED rather than becoming success.
    """

    status = reply.metadata.get("status")
    if status == "failed":
        return ExecutionOutcome.TECHNICAL_FAILURE
    if reply.needs_clarification:
        return ExecutionOutcome.CLARIFICATION_REQUIRED
    if status == "empty":
        return ExecutionOutcome.NO_DATA
    if (
        status is None
        and reply.metadata.get("intent") in _SUPPORTED_INTENTS
        and isinstance(reply.metadata.get("structured_result"), dict)
    ):
        return ExecutionOutcome.SUCCESS
    return ExecutionOutcome.UNCLASSIFIED


def execute_plan(
    state: ConversationState,
    planning: PlanningResult,
    orchestrator: "AgentOrchestrator",
) -> ExecutionBatch:
    """Execute eligible tasks sequentially using the supplied handle() method.

    Validate all references before the first call, then recheck current status,
    slots, issues, and decision equality immediately before each task. Stale or
    finished tasks are recorded as SKIPPED, never automatically retried.

    Only confirmed success/failure calls Memory.mark_task_result; its returned
    state is retained. Clarification, empty data, and unclassified replies keep
    the current status unchanged and expose unresolved_task_ids. Integration
    must hold these tasks for resolution rather than blindly resubmitting them.
    No input state, plan, decision, or returned reply is mutated.
    """

    snapshot = deepcopy(planning)
    current_state = deepcopy(state)
    # Reuse Phase 1 reference checks, including duplicate IDs and the task limit.
    # Invalid references abort before executing any otherwise valid task.
    build_plan(current_state, snapshot.task_ids)
    notices = list(snapshot.notices)
    results = []

    for planned in snapshot.planned_tasks:
        task = next(task for task in current_state.tasks if task.task_id == planned.task_id)
        blockers = _task_notices(task)
        if task.decision != planned.decision:
            blockers.append(
                PlanningNotice(
                    task.task_id,
                    "stale_plan",
                    f"Task {task.task_id} changed since planning; "
                    "rebuild its plan before execution.",
                )
            )
        if blockers:
            notices.extend(blockers)
            results.append(
                TaskExecutionResult(
                    task_id=planned.task_id,
                    decision=deepcopy(planned.decision),
                    outcome=ExecutionOutcome.SKIPPED,
                    message=" ".join(notice.message for notice in blockers),
                )
            )
            continue

        reply = None
        error_type = None
        message = None
        try:
            returned_reply = orchestrator.handle(deepcopy(planned.decision))
            if not isinstance(returned_reply, AgentReply):
                raise TypeError("handle() must return AgentReply")
            reply = deepcopy(returned_reply)
            outcome = classify_reply(reply)
        except Exception as exc:
            # Isolate an individual tool failure; Memory errors below are not
            # swallowed or mislabeled as tool failures. Never catch BaseException.
            outcome = ExecutionOutcome.TECHNICAL_FAILURE
            error_type = type(exc).__name__
            message = "Task execution raised an exception; no business conclusion is available."

        state_updated = outcome in {
            ExecutionOutcome.SUCCESS,
            ExecutionOutcome.TECHNICAL_FAILURE,
        }
        if state_updated:
            current_state = mark_task_result(
                current_state,
                planned.task_id,
                success=outcome == ExecutionOutcome.SUCCESS,
            )
        else:
            message = (
                f"Outcome {outcome.value} recorded; Memory status unchanged. "
                "Integration must resolve this task before resubmission."
            )

        results.append(
            TaskExecutionResult(
                task_id=planned.task_id,
                decision=deepcopy(planned.decision),
                outcome=outcome,
                reply=reply,
                message=message,
                error_type=error_type,
                state_updated=state_updated,
            )
        )

    return ExecutionBatch(current_state, tuple(results), tuple(notices))
