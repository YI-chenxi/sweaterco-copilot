"""Person 4 integration adapters; Person 3/5 implementation files are untouched.

Validation remains pure. These adapters return detached state/reply snapshots,
use existing Memory updates, and call Person 5's public entry points unchanged.
"""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from app.agent.contracts import AgentReply
from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    SlotUpdate,
    TaskStatus,
)
from app.agent.enhancement.conversation.clarification import (
    TERMINAL_STATUSES,
    ClarificationResult,
    ValidationPolicy,
    normalize_slot,
    validate_request,
)
from app.agent.enhancement.conversation.memory import ALLOWED_SLOTS, apply_slot_update
from app.agent.enhancement.planning.planner import (
    ExecutionBatch,
    PlanningResult,
    build_plan,
    execute_plan,
)
from app.agent.enhancement.planning.response import aggregate_results
from app.agent.intents import ResponseLanguage, find_missing_slots

if TYPE_CHECKING:
    from app.agent.orchestrator import AgentOrchestrator


@dataclass(frozen=True)
class ValidatedExecution:
    """Retain state for the NEXT turn; never reuse the pre-execution input state."""

    state: ConversationState
    reply: AgentReply
    clarification: ClarificationResult
    planning: PlanningResult | None = None
    execution: ExecutionBatch | None = None


def prepare_state(
    state: ConversationState, clarification: ClarificationResult
) -> ConversationState:
    """Persist task blockers on a new snapshot before planning.

    Reject stale validation. Global problems are turn-level and are NOT assigned
    to arbitrary tasks; run_validated_tasks aborts planning for that whole turn.
    No terminal task is reopened, and PENDING tasks are never auto-promoted.
    """

    updated = deepcopy(state)
    task_map = {task.task_id: task for task in updated.tasks}
    if len(task_map) != len(updated.tasks):
        raise ValueError("Duplicate task ID in conversation state")
    for item in clarification.tasks:
        task = task_map.get(item.source.task_id)
        if task != item.source:
            raise ValueError("State changed after validation; validate again")
        if task.status in TERMINAL_STATUSES:
            continue
        task.decision = replace(task.decision, slots=deepcopy(item.normalized_slots))
        task.decision = replace(task.decision, missing_slots=find_missing_slots(task.decision))
        for problem in item.problems:
            if problem.issue is not None and problem.issue not in task.issues:
                task.issues.append(deepcopy(problem.issue))
        if item.problems:
            task.status = TaskStatus.PENDING
    return updated


def apply_validated_update(
    state: ConversationState,
    update: SlotUpdate,
    *,
    policy: ValidationPolicy,
    language: ResponseLanguage | None = None,
) -> tuple[ConversationState, ClarificationResult]:
    """Check updates BEFORE Memory clears matching old issues.

    Legal fields use Memory's existing apply_slot_update. Invalid values do not
    overwrite previously collected values and become task issues. None explicitly
    removes a field. An empty/invalid response cannot resume a held task.
    """

    targets = [task for task in state.tasks if task.task_id == update.task_id]
    if len(targets) != 1:
        raise ValueError("Update must identify exactly one existing task")
    target = targets[0]
    if target.status in TERMINAL_STATUSES:
        raise ValueError("Use an explicit Memory retry before updating a terminal task")
    invalid_fields = set(update.slots) - ALLOWED_SLOTS[target.decision.intent]
    if invalid_fields:
        raise ValueError(f"Unsupported update slots: {sorted(invalid_fields)}")
    valid = {}
    invalid = []
    for field, value in update.slots.items():
        normalized = None if value is None else normalize_slot(field, value, policy)
        if value is None or normalized is not None:
            valid[field] = normalized
        else:
            invalid.append(
                InterpretationIssue(
                    InterpretationIssueType.INVALID_FORMAT,
                    field=field,
                    original_value=deepcopy(value),
                    task_id=update.task_id,
                )
            )
    updated = (
        apply_slot_update(state, SlotUpdate(update.task_id, valid)) if valid else deepcopy(state)
    )
    result = validate_request(
        updated, [update.task_id], policy=policy, issues=invalid, language=language
    )
    return prepare_state(updated, result), result


def attach_clarification(reply: AgentReply, result: ClarificationResult) -> AgentReply:
    """Replace generic waiting questions without altering source replies/evidence."""

    updated = deepcopy(reply)
    prior_questions = deepcopy(updated.metadata.get("clarification_questions", []))
    task_records = updated.metadata.get("tasks", {})
    for item in result.tasks:
        if not item.problems:
            continue
        task_id = item.source.task_id
        questions = [problem.question for problem in item.problems if problem.question]
        record = task_records.get(task_id)
        if record is not None:
            # Only replace fallback prose from waiting tasks, never a tool reply.
            if record.get("reply") is None:
                for old in record.get("clarification_questions", []):
                    updated.text = updated.text.replace(old, "")
            record["needs_clarification"] = bool(questions)
            record["clarification_questions"] = questions
        prior_questions = [q for q in prior_questions if q.get("task_id") != task_id]
        prior_questions.extend(
            {"task_id": task_id, "question": question, "required": True} for question in questions
        )
    global_questions = [problem.question for problem in result.global_problems if problem.question]
    prior_questions.extend(
        {"task_id": None, "question": question, "required": True} for question in global_questions
    )
    order = {task_id: index for index, task_id in enumerate(result.task_ids)}
    prior_questions.sort(key=lambda q: order.get(q.get("task_id"), -1))
    updated.metadata["clarification_questions"] = prior_questions
    updated.metadata["clarification"] = {
        "blocks_request": result.blocks_request,
        "executable_task_ids": list(result.executable_task_ids),
        "problems": [asdict(problem) for problem in result.problems],
        "source_questions": deepcopy(reply.metadata.get("clarification_questions", [])),
    }
    active = []
    for question in prior_questions:
        if not question.get("required"):
            continue
        label = f"[{question['task_id']}] " if question.get("task_id") else ""
        rendered = label + question["question"]
        if rendered not in active:
            active.append(rendered)
    updated.needs_clarification = bool(active)
    updated.suggested_question = "\n".join(active) or None
    specific = []
    for problem in result.problems:
        if not problem.question:
            continue
        label = f"[{problem.task_id}] " if problem.task_id else ""
        rendered = label + problem.question
        if rendered not in specific:
            specific.append(rendered)
    holds = [
        f"[{problem.task_id}] {problem.reason}"
        for problem in result.problems
        if problem.question is None
    ]
    if holds:
        updated.text = updated.text.rstrip() + "\n\n" + "\n".join(holds)
    if specific:
        heading = "澄清信息" if result.language == ResponseLanguage.CHINESE else "Clarification"
        updated.text = updated.text.rstrip() + "\n\n" + heading + "\n" + "\n".join(specific)
    return updated


def hold_unresolved_tasks(execution: ExecutionBatch) -> ConversationState:
    """Pause no-data/clarification/unclassified outcomes AFTER aggregation.

    Person 5 aggregation expects its original READY snapshot for these outcomes.
    Never edit ExecutionBatch.state before aggregate_results. A held PENDING task
    needs an explicit validated update or integration review before resubmission;
    no-data and technical uncertainty are not fabricated as NLU format errors.
    """

    updated = deepcopy(execution.state)
    for task in updated.tasks:
        if task.task_id in execution.unresolved_task_ids and task.status == TaskStatus.READY:
            task.status = TaskStatus.PENDING
    return updated


def run_validated_tasks(
    state: ConversationState,
    task_ids: Sequence[str],
    orchestrator: "AgentOrchestrator",
    *,
    policy: ValidationPolicy,
    issues: Sequence[InterpretationIssue] = (),
    decision_task_ids: Sequence[str] = (),
    is_ambiguous: bool = False,
    language: ResponseLanguage | None = None,
) -> ValidatedExecution:
    """One explicit validated batch, not an AgentService/UI replacement.

    New tasks must already have IDs; apply updates through apply_validated_update.
    Pass unresolved GLOBAL issues on every turn until the upstream reference is
    resolved. Dropping them is not confirmation. Revalidate after any state edit.
    """

    reference = getattr(getattr(orchestrator, "settings", None), "reference_date", None)
    if reference is not None and reference != policy.reference_date:
        raise ValueError("Validation and execution must use the same reference_date")
    result = validate_request(
        state,
        task_ids,
        policy=policy,
        issues=issues,
        decision_task_ids=decision_task_ids,
        is_ambiguous=is_ambiguous,
        language=language,
    )
    prepared = prepare_state(state, result)
    if result.blocks_request:
        text = (
            "本轮请求需先澄清，尚未执行任何任务。"
            if result.language == ResponseLanguage.CHINESE
            else "This request needs clarification; no tasks were executed."
        )
        reply = attach_clarification(
            AgentReply(text, metadata={"task_order": list(result.task_ids)}), result
        )
        return ValidatedExecution(prepared, reply, result)
    planning = build_plan(prepared, result.task_ids)
    execution = execute_plan(prepared, planning, orchestrator)
    reply = aggregate_results(planning, execution, result.language, task_ids=result.task_ids)
    reply = attach_clarification(reply, result)
    next_state = hold_unresolved_tasks(execution)
    reply.metadata["clarification"]["held_task_ids"] = list(execution.unresolved_task_ids)
    reply.metadata["clarification"]["next_task_statuses"] = {
        task.task_id: task.status.value
        for task in next_state.tasks
        if task.task_id in result.task_ids
    }
    return ValidatedExecution(next_state, reply, result, planning, execution)
