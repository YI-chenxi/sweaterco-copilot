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
_SLOT_LABELS = {
    ResponseLanguage.ENGLISH: {
        "category": "product category",
        "order_id": "order number",
        "pieces": "order quantity",
        "requested_delivery_date": "delivery date",
        "stage": "production stage",
        "top_n": "number of briefing items",
    },
    ResponseLanguage.CHINESE: {
        "category": "产品类别",
        "order_id": "订单号",
        "pieces": "订单数量",
        "requested_delivery_date": "期望交付日期",
        "stage": "生产工序",
        "top_n": "晨报条数",
    },
}


def _headline(text: str) -> str:
    return f"**{text}**"


def _join_labels(labels: list[str], language: ResponseLanguage) -> str:
    if language == ResponseLanguage.CHINESE:
        return "、".join(labels)
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def _request_name(decision: dict[str, Any], language: ResponseLanguage) -> str:
    """Describe a task using business language, never an internal task ID."""

    intent = decision.get("intent")
    slots = decision.get("slots") if isinstance(decision.get("slots"), dict) else {}
    order_id = slots.get("order_id")
    stage = slots.get("stage")
    category = slots.get("category")
    pieces = slots.get("pieces")
    if intent == "order_status":
        if language == ResponseLanguage.CHINESE:
            return f"订单 {order_id}" if order_id else "订单查询"
        return f"Order {order_id}" if order_id else "Order check"
    if intent == "production_normality":
        if language == ResponseLanguage.CHINESE:
            return f"{stage} 工序" if stage else "生产情况检查"
        return f"{stage} production" if stage else "Production check"
    if intent == "order_feasibility":
        if language == ResponseLanguage.CHINESE:
            details = " ".join(
                value
                for value in (
                    f"{pieces} 件" if pieces is not None else "",
                    str(category) if category is not None else "",
                )
                if value
            )
            return f"新增 {details} 订单" if details else "新增订单评估"
        details = " ".join(
            value
            for value in (
                f"{pieces}-piece" if pieces is not None else "",
                str(category) if category is not None else "",
            )
            if value
        )
        return f"Additional {details} order" if details else "Additional order assessment"
    if intent == "morning_briefing":
        return "晨报" if language == ResponseLanguage.CHINESE else "Morning briefing"
    return "这个请求" if language == ResponseLanguage.CHINESE else "This request"


def _fallback_question(
    decision: IntentDecision, missing: tuple[str, ...], language: ResponseLanguage
) -> str:
    """Turn missing fields into one natural, task-aware follow-up question."""

    slots = decision.slots
    if decision.intent.value == "order_status" and "order_id" in missing:
        return (
            "你想查询哪个订单？"
            if language == ResponseLanguage.CHINESE
            else "Which order would you like me to check?"
        )
    if decision.intent.value == "production_normality" and "stage" in missing:
        return (
            "你想检查哪个生产工序？"
            if language == ResponseLanguage.CHINESE
            else "Which production stage would you like me to check?"
        )
    if decision.intent.value == "order_feasibility":
        pieces = slots.get("pieces")
        category = slots.get("category")
        if missing == ("requested_delivery_date",):
            if language == ResponseLanguage.CHINESE:
                details = " ".join(
                    value
                    for value in (
                        f"{pieces} 件" if pieces is not None else "",
                        str(category) if category is not None else "",
                    )
                    if value
                )
                subject = f"这笔 {details} 订单" if details else "这笔新增订单"
                return f"{subject}的期望交付日期是什么时候？"
            details = " ".join(
                value
                for value in (
                    f"{pieces}-piece" if pieces is not None else "",
                    str(category) if category is not None else "",
                )
                if value
            )
            subject = f"the {details} order" if details else "this additional order"
            return f"What delivery date should I use for {subject}?"
        if missing == ("category",):
            if language == ResponseLanguage.CHINESE:
                return (
                    f"这笔 {pieces} 件订单属于哪个产品类别？"
                    if pieces
                    else "这笔订单属于哪个产品类别？"
                )
            return (
                f"What product category is the {pieces}-piece order for?"
                if pieces
                else "What product category is this order for?"
            )
        if missing == ("pieces",):
            if language == ResponseLanguage.CHINESE:
                return f"这笔 {category or ''} 订单有多少件？".replace("  ", " ")
            return f"How many pieces are in the {category or 'new'} order?"
    if decision.intent.value == "morning_briefing" and "top_n" in missing:
        return (
            "你希望晨报列出多少项重点？"
            if language == ResponseLanguage.CHINESE
            else "How many priority items would you like in the briefing?"
        )
    labels = [_SLOT_LABELS[language].get(field, field) for field in missing]
    if labels:
        rendered = _join_labels(labels, language)
        return (
            f"请告诉我{rendered}。"
            if language == ResponseLanguage.CHINESE
            else f"Could you provide the {rendered}?"
        )
    return (
        "请再说明一下你希望我处理的内容。"
        if language == ResponseLanguage.CHINESE
        else "Could you clarify what you would like me to check?"
    )


def _safe_status_message(record: dict[str, Any], language: ResponseLanguage) -> str:
    """Explain a non-result without exposing planner or memory terminology."""

    status = record.get("display_status")
    task_status = record.get("task_status")
    if status == "failed":
        message = (
            "抱歉，我现在无法完成这个查询。请稍后重试。"
            if language == ResponseLanguage.CHINESE
            else "Sorry, I couldn't complete this request right now. Please try again."
        )
        return _headline(message)
    if status == "skipped" and task_status == "completed":
        message = (
            "这个请求之前已经完成，因此这次没有重复执行。"
            if language == ResponseLanguage.CHINESE
            else "This request was already completed, so I didn't run it again."
        )
        return _headline(message)
    if status == "skipped" and task_status == "failed":
        message = (
            "这个请求之前没有成功完成。如果需要重试，请重新描述你的需求。"
            if language == ResponseLanguage.CHINESE
            else "This request wasn't completed earlier. Please restate it if you'd like to retry."
        )
        return _headline(message)
    if status == "skipped":
        message = (
            "请求内容在处理过程中发生了变化，请重新发送一次。"
            if language == ResponseLanguage.CHINESE
            else "The request details changed while I was processing them. Please send it again."
        )
        return _headline(message)
    if status in {"pending", "blocked", "clarification_required"}:
        message = (
            "继续处理前，我还需要一点信息。"
            if language == ResponseLanguage.CHINESE
            else "I need a little more information before I can continue."
        )
        return _headline(message)
    message = (
        "这次没有得到可验证的结果，请稍后重试。"
        if language == ResponseLanguage.CHINESE
        else "I couldn't produce a verified answer this time. Please try again."
    )
    return _headline(message)


def render_user_text(metadata: dict[str, Any], language: ResponseLanguage) -> str:
    """Render public conversation text from structured records.

    Internal task IDs, enum names, planning notices, and exception details remain
    available in metadata but never enter this user-facing string.
    """

    task_order = metadata.get("task_order", [])
    task_records = metadata.get("tasks", {})
    sections: list[tuple[str, list[str]]] = []
    has_result = False
    has_question = False
    for task_id in task_order:
        record = task_records.get(task_id)
        if not isinstance(record, dict):
            continue
        parts: list[str] = []
        source = record.get("reply")
        if isinstance(source, dict):
            source_text = source.get("text")
            if isinstance(source_text, str) and source_text.strip():
                parts.append(source_text.strip())
                if not record.get("needs_clarification"):
                    has_result = True
        for question in record.get("clarification_questions", []):
            if isinstance(question, str) and question.strip() and question.strip() not in parts:
                parts.append(question.strip())
                has_question = True
        if not parts:
            parts.append(_safe_status_message(record, language))
        decision = record.get("current_decision") or record.get("decision") or {}
        sections.append((_request_name(decision, language), parts))

    global_questions = [
        item.get("question", "").strip()
        for item in metadata.get("clarification_questions", [])
        if item.get("task_id") is None
        and item.get("required")
        and isinstance(item.get("question"), str)
        and item.get("question", "").strip()
    ]
    if not sections:
        if global_questions:
            conclusion = (
                "继续处理前，我需要你先确认一件事。"
                if language == ResponseLanguage.CHINESE
                else "I need you to clarify one thing before I can continue."
            )
            return _headline(conclusion) + "\n\n" + "\n\n".join(dict.fromkeys(global_questions))
        message = (
            "这次没有需要处理的新请求。"
            if language == ResponseLanguage.CHINESE
            else "There isn't a new request to handle yet."
        )
        return _headline(message)
    if len(sections) == 1 and not global_questions:
        only_record = next(
            (
                task_records.get(task_id)
                for task_id in task_order
                if isinstance(task_records.get(task_id), dict)
            ),
            None,
        )
        if (
            isinstance(only_record, dict)
            and not isinstance(only_record.get("reply"), dict)
            and only_record.get("clarification_questions")
        ):
            conclusion = (
                "继续处理前，我还需要一点信息。"
                if language == ResponseLanguage.CHINESE
                else "I need one more detail before I can continue."
            )
            return _headline(conclusion) + "\n\n" + "\n\n".join(sections[0][1])
        return "\n\n".join(sections[0][1])

    if has_result and has_question:
        intro = (
            "我先处理了信息完整的部分，另外还需要补充一点信息："
            if language == ResponseLanguage.CHINESE
            else "I completed what I could and need one more detail for the rest:"
        )
    elif has_question:
        intro = (
            "继续处理前，我还需要这些信息："
            if language == ResponseLanguage.CHINESE
            else "I need a couple of details before I can continue:"
        )
    else:
        intro = (
            "我分别处理了这些内容："
            if language == ResponseLanguage.CHINESE
            else "Here's what I found:"
        )
    lines = [_headline(intro)]
    for index, (name, parts) in enumerate(sections, 1):
        lines.append(
            f"{index}. {name}：{' '.join(parts)}"
            if language == ResponseLanguage.CHINESE
            else f"{index}. {name}: {' '.join(parts)}"
        )
    lines.extend(global_questions)
    return "\n\n".join(lines)


def render_suggested_question(metadata: dict[str, Any], language: ResponseLanguage) -> str | None:
    """Keep task association in metadata while presenting business context to users."""

    required = [
        item
        for item in metadata.get("clarification_questions", [])
        if item.get("required") and isinstance(item.get("question"), str)
    ]
    if not required:
        return None
    if len(required) == 1:
        return required[0]["question"]
    records = metadata.get("tasks", {})
    rendered = []
    for item in required:
        question = item["question"]
        record = records.get(item.get("task_id"))
        if isinstance(record, dict):
            decision = record.get("current_decision") or record.get("decision") or {}
            name = _request_name(decision, language)
            line = (
                f"{name}：{question}"
                if language == ResponseLanguage.CHINESE
                else f"{name}: {question}"
            )
        else:
            line = question
        if line not in rendered:
            rendered.append(line)
    return "\n".join(rendered) or None


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
        questions.append(_fallback_question(task.decision, missing, language))
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
        if result and result.reply:
            aggregated.evidence.extend(deepcopy(result.reply.evidence))
            aggregated.warnings.extend(deepcopy(result.reply.warnings))
            aggregated.assumptions.extend(deepcopy(result.reply.assumptions))
    aggregated.text = render_user_text(aggregated.metadata, language)
    aggregated.suggested_question = render_suggested_question(aggregated.metadata, language)
    return aggregated
