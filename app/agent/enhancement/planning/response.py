"""Pure result aggregation; no tool execution, state transitions, or translation.

Original requested task_ids are required because PlanningResult splits executable
entries and notices, losing their interleaving. Pass the same IDs as build_plan.
Source content is preserved; only headings and fallback explanations are localized.
"""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from app.agent.contracts import AgentReply
from app.agent.enhancement.contracts import TaskState, TaskStatus
from app.agent.enhancement.planning.planner import (
    ExecutionBatch,
    ExecutionOutcome,
    PlanningNotice,
    PlanningResult,
    TaskExecutionResult,
    classify_reply,
)
from app.agent.intents import IntentDecision, ResponseLanguage

_INFORMATION_CODES = frozenset(
    {"pending", "missing_slots", "unresolved_issues", "unsupported_intent"}
)
_LABELS = {
    ResponseLanguage.ENGLISH: {
        "completed": "Completed",
        "failed": "Execution failed",
        "pending": "Waiting for information or validation",
        "blocked": "Blocked",
        "clarification_required": "Clarification required",
        "no_data": "No data returned",
        "unclassified": "Result requires integration review",
        "skipped": "Skipped; not executed in this batch",
        "not_executed": "Not executed",
    },
    ResponseLanguage.CHINESE: {
        "completed": "已完成",
        "failed": "执行失败",
        "pending": "等待补充信息或校验",
        "blocked": "暂不能执行",
        "clarification_required": "需要澄清",
        "no_data": "未返回可用数据",
        "unclassified": "结果需要集成层确认",
        "skipped": "已跳过，本批次未执行",
        "not_executed": "尚未执行",
    },
}


def _index(items: Sequence[Any], name: str) -> dict[str, Any]:
    indexed = {}
    for item in items:
        task_id = item.task_id
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"Invalid task ID in {name}")
        if task_id in indexed:
            raise ValueError(f"Duplicate task ID in {name}: {task_id}")
        indexed[task_id] = item
    return indexed


def _decision_data(decision: IntentDecision) -> dict[str, Any]:
    return {
        "intent": decision.intent.value,
        "slots": deepcopy(decision.slots),
        "missing_slots": list(decision.missing_slots),
        "language": decision.language.value,
    }


def _validate_result(
    result: TaskExecutionResult, decision: IntentDecision, task: TaskState
) -> None:
    if result.decision != decision:
        raise ValueError(f"Result decision differs from plan: {result.task_id}")
    if not isinstance(result.outcome, ExecutionOutcome):
        raise ValueError(f"Invalid execution outcome: {result.task_id}")
    terminal_status = {
        ExecutionOutcome.SUCCESS: TaskStatus.COMPLETED,
        ExecutionOutcome.TECHNICAL_FAILURE: TaskStatus.FAILED,
    }.get(result.outcome)
    if result.error_type and result.outcome != ExecutionOutcome.TECHNICAL_FAILURE:
        raise ValueError(f"Exception record has a non-failure outcome: {result.task_id}")
    if result.state_updated != (terminal_status is not None):
        raise ValueError(f"Inconsistent state_updated flag: {result.task_id}")
    if terminal_status is not None and task.status != terminal_status:
        raise ValueError(f"Result and Memory status disagree: {result.task_id}")
    if result.outcome == ExecutionOutcome.SKIPPED:
        if result.reply is not None:
            raise ValueError(f"Skipped task cannot have an execution reply: {result.task_id}")
        return
    if task.decision != result.decision:
        raise ValueError(f"Executed decision differs from Memory: {result.task_id}")
    if terminal_status is None and task.status != TaskStatus.READY:
        raise ValueError(f"Unresolved result changed Memory status: {result.task_id}")
    if result.reply is None:
        if result.outcome != ExecutionOutcome.TECHNICAL_FAILURE or not result.error_type:
            raise ValueError(f"Missing execution reply: {result.task_id}")
        return
    source_intent = result.reply.metadata.get("intent")
    if source_intent is not None and source_intent != result.decision.intent.value:
        raise ValueError(f"Reply intent differs from task intent: {result.task_id}")
    # Phase 2 can retain a malformed reply when classification itself raised.
    # Preserve that technical failure record rather than inventing a new result.
    if not result.error_type and classify_reply(result.reply) != result.outcome:
        raise ValueError(f"Reply signals disagree with execution outcome: {result.task_id}")


def _task_questions(
    task_id: str,
    notices: list[PlanningNotice],
    result: TaskExecutionResult | None,
    task: TaskState,
    language: ResponseLanguage,
) -> tuple[bool, list[str]]:
    questions = []
    if result and result.reply and result.reply.suggested_question:
        questions.append(result.reply.suggested_question)
    if result is not None:
        needs_information = result.outcome == ExecutionOutcome.CLARIFICATION_REQUIRED
        if result.outcome == ExecutionOutcome.SKIPPED:
            needs_information = task.status not in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            } and any(notice.code in _INFORMATION_CODES for notice in notices)
    else:
        needs_information = task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED} and any(
            notice.code in _INFORMATION_CODES for notice in notices
        )
    if not needs_information:
        return False, questions
    if not questions:
        missing = tuple(dict.fromkeys(slot for notice in notices for slot in notice.missing_slots))
        if missing:
            fields = ", ".join(missing)
            question = (
                f"请补充任务 {task_id} 的以下信息：{fields}。"
                if language == ResponseLanguage.CHINESE
                else f"Please provide the following information for task {task_id}: {fields}."
            )
        else:
            question = (
                f"请确认或补充任务 {task_id} 尚未明确的信息。"
                if language == ResponseLanguage.CHINESE
                else f"Please clarify the unresolved information for task {task_id}."
            )
        questions.append(question)
    return True, questions


def aggregate_results(
    planning: PlanningResult,
    execution: ExecutionBatch,
    language: ResponseLanguage,
    *,
    task_ids: Sequence[str],
) -> AgentReply:
    """Combine at most two tasks in explicit original request order.

    Use the original task_ids passed to build_plan, including waiting/skipped
    tasks. An entirely empty execution batch is valid: planned tasks are labeled
    not_executed, never completed. A partially missing set of execution records,
    unexpected/duplicate IDs, or inconsistent outcomes raises ValueError.

    Top-level evidence/warnings/assumptions are concatenated without deduplication
    in request order. metadata.tasks[task_id].reply retains every source field,
    preserving ownership and metadata collisions. Questions are associated with
    IDs; only genuine information needs populate the combined suggested_question.
    No input objects are mutated, and no historical result is fabricated.
    """

    if not isinstance(language, ResponseLanguage):
        raise ValueError("language must be a ResponseLanguage")
    if isinstance(task_ids, (str, bytes)):
        raise ValueError("task_ids must be a sequence, not a string")
    requested = tuple(task_ids)
    if len(requested) > 2:
        raise ValueError("At most two tasks may be aggregated")
    if any(not isinstance(task_id, str) or not task_id.strip() for task_id in requested):
        raise ValueError("Task IDs must be non-empty strings")
    if len(set(requested)) != len(requested):
        raise ValueError("Duplicate requested task IDs")

    planned = _index(planning.planned_tasks, "plan")
    results = _index(execution.results, "execution results")
    states = _index(execution.state.tasks, "conversation state")
    planning_notice_ids = {notice.task_id for notice in planning.notices}
    if set(planned) & planning_notice_ids:
        raise ValueError("A task cannot be both planned and blocked/skipped in the initial plan")
    if set(requested) != set(planned) | planning_notice_ids:
        raise ValueError("Requested IDs do not match the original plan and notices")
    if not set(requested) <= set(states):
        raise ValueError("Requested task is missing from conversation state")
    if not set(results) <= set(planned):
        raise ValueError("Execution result refers to a task outside the executable plan")
    if results and set(results) != set(planned):
        raise ValueError("Execution batch is missing some planned task results")
    notices = list(planning.notices)
    for notice in execution.notices:
        if notice.task_id not in requested:
            raise ValueError("Execution notice refers to an unrequested task")
        if notice not in notices:
            notices.append(notice)
    if any(not isinstance(states[task_id].status, TaskStatus) for task_id in requested):
        raise ValueError("Invalid task status in conversation state")
    for task_id, result in results.items():
        _validate_result(result, planned[task_id].decision, states[task_id])

    aggregated = AgentReply(
        text="",
        metadata={
            "language": language.value,
            "task_order": list(requested),
            "tasks": {},
            "clarification_questions": [],
            "unresolved_task_ids": list(execution.unresolved_task_ids),
        },
    )
    sections = []
    active_questions = []
    for task_id in requested:
        task = states[task_id]
        result = results.get(task_id)
        task_notices = [notice for notice in notices if notice.task_id == task_id]
        decision = planned[task_id].decision if task_id in planned else task.decision
        if result:
            display_status = {
                ExecutionOutcome.SUCCESS: "completed",
                ExecutionOutcome.TECHNICAL_FAILURE: "failed",
            }.get(result.outcome, result.outcome.value)
        elif task_id in planned:
            display_status = "not_executed"
        elif task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            display_status = "skipped"
        else:
            display_status = "pending" if task.status == TaskStatus.PENDING else "blocked"
        needs_information, questions = _task_questions(
            task_id, task_notices, result, task, language
        )
        aggregated.needs_clarification |= needs_information
        for question in questions:
            aggregated.metadata["clarification_questions"].append(
                {
                    "task_id": task_id,
                    "question": question,
                    "required": needs_information,
                }
            )
            if needs_information:
                active_questions.append(f"[{task_id}] {question}")

        # Keep every source field in a separate namespace; never flatten metadata.
        aggregated.metadata["tasks"][task_id] = {
            "task_id": task_id,
            "intent": decision.intent.value,
            "decision": _decision_data(decision),
            "current_decision": _decision_data(task.decision),
            "task_status": task.status.value,
            "execution_outcome": result.outcome.value if result else None,
            "display_status": display_status,
            "state_updated": result.state_updated if result else False,
            "message": result.message if result else None,
            "error_type": result.error_type if result else None,
            "reply": deepcopy(asdict(result.reply)) if result and result.reply else None,
            "notices": [deepcopy(asdict(notice)) for notice in task_notices],
            "needs_clarification": needs_information,
            "clarification_questions": list(questions),
        }
        task_label = "任务" if language == ResponseLanguage.CHINESE else "Task"
        lines = [
            f"{task_label} {task_id} ({decision.intent.value}) — "
            f"{_LABELS[language][display_status]}"
        ]
        if result and result.reply:
            lines.append(result.reply.text)
            aggregated.evidence.extend(deepcopy(result.reply.evidence))
            aggregated.warnings.extend(deepcopy(result.reply.warnings))
            aggregated.assumptions.extend(deepcopy(result.reply.assumptions))
        elif result and result.message:
            lines.append(result.message)
        if result is None or result.outcome == ExecutionOutcome.SKIPPED:
            lines.extend(notice.message for notice in task_notices)
        if needs_information:
            lines.extend(questions)
        sections.append("\n".join(lines))

    aggregated.text = (
        "\n\n".join(sections)
        if sections
        else (
            "本次没有请求处理的任务。"
            if language == ResponseLanguage.CHINESE
            else "No tasks were requested."
        )
    )
    aggregated.suggested_question = "\n".join(active_questions) or None
    return aggregated
