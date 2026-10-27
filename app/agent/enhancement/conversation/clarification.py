"""Pure bilingual clarification; no LLM, business tools, or state mutations."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    TaskState,
    TaskStatus,
)
from app.agent.enhancement.conversation.memory import ALLOWED_SLOTS
from app.agent.enhancement.understanding.normalizers import (
    normalize_explicit_date,
    normalize_order_id,
    normalize_positive_integer,
    normalize_stage,
    normalize_upper_token,
)
from app.agent.intents import Intent, ResponseLanguage, find_missing_slots

TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})
_FIELDS = {
    "order_id": ("订单号", "order ID"),
    "stage": ("生产工序", "production stage"),
    "category": ("产品类别", "product category"),
    "pieces": ("订单数量", "order quantity"),
    "requested_delivery_date": ("交付日期", "delivery date"),
    "top_n": ("晨报条数", "briefing item count"),
}


@dataclass(frozen=True)
class ValidationPolicy:
    """Explicit business snapshot policy; never read the system clock."""

    reference_date: date
    allowed_categories: frozenset[str] = frozenset({"TOPS", "ACCESSORIES"})

    def __post_init__(self) -> None:
        if not isinstance(self.reference_date, date) or isinstance(self.reference_date, datetime):
            raise ValueError("reference_date must be a date")
        if not self.allowed_categories or any(
            not isinstance(value, str) or normalize_upper_token(value) != value
            for value in self.allowed_categories
        ):
            raise ValueError("allowed_categories must contain canonical uppercase tokens")
        object.__setattr__(self, "allowed_categories", frozenset(self.allowed_categories))


@dataclass(frozen=True)
class ClarificationProblem:
    """A scoped user question or an explicit technical/integration hold."""

    task_id: str | None
    field: str | None
    code: str
    reason: str
    question: str | None
    issue: InterpretationIssue | None = None


@dataclass(frozen=True)
class TaskValidation:
    """Detached snapshot and safe formatting normalizations for one task."""

    source: TaskState
    normalized_slots: dict[str, Any]
    problems: tuple[ClarificationProblem, ...] = ()

    @property
    def passed(self) -> bool:
        return self.source.status not in TERMINAL_STATUSES and not self.problems


@dataclass(frozen=True)
class ClarificationResult:
    """Module-local output; shared conversation contracts are reused unchanged."""

    task_ids: tuple[str, ...]
    tasks: tuple[TaskValidation, ...]
    global_problems: tuple[ClarificationProblem, ...]
    language: ResponseLanguage

    @property
    def blocks_request(self) -> bool:
        return bool(self.global_problems)

    @property
    def problems(self) -> tuple[ClarificationProblem, ...]:
        return self.global_problems + tuple(p for task in self.tasks for p in task.problems)

    @property
    def needs_clarification(self) -> bool:
        return any(problem.question for problem in self.problems)

    @property
    def executable_task_ids(self) -> tuple[str, ...]:
        if self.blocks_request:
            return ()
        return tuple(
            item.source.task_id
            for item in self.tasks
            if item.passed and item.source.status == TaskStatus.READY
        )


def _text(language: ResponseLanguage, zh: str, en: str) -> str:
    return zh if language == ResponseLanguage.CHINESE else en


def _field_label(field: str | None, language: ResponseLanguage) -> str:
    labels = _FIELDS.get(field, (field or "信息", field or "information"))
    return labels[0] if language == ResponseLanguage.CHINESE else labels[1]


def _invalid_hint(field: str | None, policy: ValidationPolicy, language: ResponseLanguage) -> str:
    categories = ", ".join(sorted(policy.allowed_categories))
    hints = {
        "order_id": (
            "请提供 ORD-057 这样的订单号。",
            "Please provide an order ID such as ORD-057.",
        ),
        "stage": (
            "请提供 KNITTING、ASSEMBLY、WASHING 或 PACKING 中的一项工序。",
            "Please choose KNITTING, ASSEMBLY, WASHING, or PACKING.",
        ),
        "category": (f"请选择产品类别：{categories}。", f"Please choose a category: {categories}."),
        "pieces": ("请提供大于零的整数数量。", "Please provide a positive whole-number quantity."),
        "top_n": (
            "请提供大于零的整数晨报条数。",
            "Please provide a positive whole-number item count.",
        ),
        "requested_delivery_date": (
            f"请提供有效的 YYYY-MM-DD 交付日期，且晚于业务参考日 {policy.reference_date}。",
            "Please provide a valid YYYY-MM-DD delivery date later than the business "
            f"reference date {policy.reference_date}.",
        ),
    }
    return _text(
        language,
        *hints.get(
            field, ("请提供该字段的合法值。", "Please provide a valid value for this field.")
        ),
    )


def _issue_problem(
    issue: InterpretationIssue, policy: ValidationPolicy, language: ResponseLanguage
) -> ClarificationProblem:
    label = _field_label(issue.field, language)
    if issue.issue_type == InterpretationIssueType.POSSIBLE_TYPO:
        if issue.suggested_value is None:
            question = _text(
                language, f"请确认正确的{label}。", f"Please confirm the correct {label}."
            )
        else:
            question = _text(
                language,
                f"你输入的{label}是 {issue.original_value!s}；"
                f"你指的是 {issue.suggested_value!s} 吗？",
                f"You entered {label} {issue.original_value!s}; did you mean "
                f"{issue.suggested_value!s}?",
            )
        reason = _text(
            language, "疑似拼写错误，尚未获得确认。", "A possible typo needs confirmation."
        )
    elif issue.issue_type == InterpretationIssueType.AMBIGUOUS_REFERENCE:
        question = _text(
            language,
            "你指的是哪个任务？请提供任务编号，或说明订单号／任务内容。",
            "Which task do you mean? Please specify its task ID, order ID, or description.",
        )
        reason = _text(language, "无法确定目标任务。", "The target task is unresolved.")
    elif issue.issue_type == InterpretationIssueType.TOO_MANY_TASKS:
        question = _text(
            language,
            "请将请求拆分为每次最多两个业务任务。",
            "Please split this request into at most two business tasks per message.",
        )
        reason = _text(
            language, "超过本轮两个任务的上限。", "The request exceeds the two-task limit."
        )
    else:
        question = _invalid_hint(issue.field, policy, language)
        reason = _text(language, f"{label}的值无效。", f"The {label} value is invalid.")
    return ClarificationProblem(
        issue.task_id, issue.field, issue.issue_type.value, reason, question, deepcopy(issue)
    )


def normalize_slot(field: str, value: Any, policy: ValidationPolicy) -> Any:
    """Reuse Person 1 normalizers, adding category/date semantic checks."""

    normalizers = {
        "order_id": normalize_order_id,
        "stage": normalize_stage,
        "category": normalize_upper_token,
        "pieces": normalize_positive_integer,
        "top_n": normalize_positive_integer,
        "requested_delivery_date": normalize_explicit_date,
    }
    normalized = normalizers[field](value)
    if field == "category" and normalized not in policy.allowed_categories:
        return None
    if field == "requested_delivery_date" and normalized is not None:
        if date.fromisoformat(normalized) <= policy.reference_date:
            return None
    return normalized


def _validate_task(
    task: TaskState,
    issues: list[InterpretationIssue],
    policy: ValidationPolicy,
    language: ResponseLanguage,
) -> TaskValidation:
    source = deepcopy(task)
    slots = deepcopy(task.decision.slots)
    if task.status in TERMINAL_STATUSES:
        return TaskValidation(source, slots)
    problems = [_issue_problem(issue, policy, language) for issue in issues]
    covered_fields = {problem.field for problem in problems if problem.field}
    if task.decision.intent == Intent.UNKNOWN:
        problems.append(
            ClarificationProblem(
                task.task_id,
                None,
                "unknown_intent",
                _text(
                    language,
                    "尚未确定支持的业务意图。",
                    "No supported business intent was identified.",
                ),
                _text(
                    language,
                    "你想查询订单、检查生产、查看晨报，还是评估新订单？",
                    "Would you like an order status, production check, "
                    "briefing, or feasibility check?",
                ),
            )
        )
    for field in find_missing_slots(task.decision):
        if field in covered_fields:
            continue
        label = _field_label(field, language)
        problems.append(
            ClarificationProblem(
                task.task_id,
                field,
                "missing_slot",
                _text(language, f"缺少{label}。", f"Missing {label}."),
                _text(language, f"请补充{label}。", f"Please provide the {label}."),
            )
        )
        covered_fields.add(field)
    for field, value in task.decision.slots.items():
        if field not in _FIELDS or field in covered_fields:
            continue
        normalized = normalize_slot(field, value, policy)
        if normalized is None:
            issue = InterpretationIssue(
                InterpretationIssueType.INVALID_FORMAT,
                field=field,
                original_value=deepcopy(value),
                task_id=task.task_id,
            )
            problems.append(_issue_problem(issue, policy, language))
        else:
            slots[field] = normalized
    if not problems and task.status != TaskStatus.READY:
        problems.append(
            ClarificationProblem(
                task.task_id,
                None,
                "pending_review",
                _text(
                    language,
                    "任务处于暂停状态，需由集成层明确解决后再重新规划。",
                    "This task is held pending explicit resolution by the integration layer.",
                ),
                None,
            )
        )
    return TaskValidation(source, slots, tuple(problems))


def validate_request(
    state: ConversationState,
    task_ids: Sequence[str],
    *,
    policy: ValidationPolicy,
    issues: Sequence[InterpretationIssue] = (),
    decision_task_ids: Sequence[str] = (),
    is_ambiguous: bool = False,
    language: ResponseLanguage | None = None,
) -> ClarificationResult:
    """Inspect selected tasks and turn issues without altering any input.

    decision_task_ids maps NLU decision_index to IDs assigned by integration.
    Unbound/unknown/out-of-scope issue targets block the whole request. Duplicate
    or absent requested IDs are integration errors. More than two tasks produce
    a global question, never a partial plan. Keep global issues until resolved.
    """

    language = language or state.language
    if not isinstance(language, ResponseLanguage):
        raise ValueError("language must be a ResponseLanguage")
    if isinstance(task_ids, (str, bytes)) or isinstance(decision_task_ids, (str, bytes)):
        raise ValueError("Task IDs must be sequences, not strings")
    requested = tuple(task_ids)
    task_map = {}
    for task in state.tasks:
        if not isinstance(task.task_id, str) or not task.task_id.strip():
            raise ValueError("Task IDs must be non-empty strings")
        if task.task_id in task_map:
            raise ValueError("Duplicate task ID in conversation state")
        if not isinstance(task.status, TaskStatus) or not isinstance(task.decision.intent, Intent):
            raise ValueError("Tasks must use the shared status and intent enums")
        if set(task.decision.slots) - ALLOWED_SLOTS[task.decision.intent]:
            raise ValueError("Task contains unsupported slot names")
        task_map[task.task_id] = task
    if any(not isinstance(key, str) or not key.strip() for key in requested):
        raise ValueError("Task IDs must be non-empty strings")
    if len(set(requested)) != len(requested):
        raise ValueError("Duplicate requested task IDs")
    if any(key not in task_map for key in requested):
        raise ValueError("Unknown requested task ID")
    assigned = tuple(decision_task_ids)
    if len(set(assigned)) != len(assigned) or any(key not in task_map for key in assigned):
        raise ValueError("Invalid decision-to-task mapping")
    bound = {key: [] for key in requested}
    global_problems = []
    task_issues = [
        replace(deepcopy(issue), task_id=key, decision_index=None)
        for key in requested
        if task_map[key].status not in TERMINAL_STATUSES
        for issue in task_map[key].issues
    ]
    for raw in [*task_issues, *issues]:
        if not isinstance(raw.issue_type, InterpretationIssueType):
            raise ValueError("Issues must use the shared issue type enum")
        issue = deepcopy(raw)
        target = issue.task_id
        if target is None and issue.decision_index is not None:
            index = issue.decision_index
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(assigned)
            ):
                target = assigned[index]
        if (
            target in bound
            and task_map[target].status not in TERMINAL_STATUSES
            and issue.issue_type != InterpretationIssueType.TOO_MANY_TASKS
        ):
            issue = replace(issue, task_id=target, decision_index=None)
            if issue not in bound[target]:
                bound[target].append(issue)
        else:
            problem = replace(_issue_problem(issue, policy, language), task_id=None)
            if problem not in global_problems:
                global_problems.append(problem)
    if len(requested) > 2 and not any(p.code == "too_many_tasks" for p in global_problems):
        global_problems.append(
            _issue_problem(
                InterpretationIssue(InterpretationIssueType.TOO_MANY_TASKS), policy, language
            )
        )
    if is_ambiguous and not issues and not any(bound.values()) and not global_problems:
        global_problems.append(
            _issue_problem(
                InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE), policy, language
            )
        )
    return ClarificationResult(
        requested,
        tuple(_validate_task(task_map[key], bound[key], policy, language) for key in requested),
        tuple(global_problems),
        language,
    )
