"""Ground-truth evaluation for enhanced natural-language understanding."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    SlotUpdate,
    TaskState,
    TaskStatus,
    UnderstandingResult,
)
from app.agent.enhancement.understanding.normalizers import (
    normalize_explicit_date,
    normalize_order_id,
    normalize_positive_integer,
    normalize_stage,
    normalize_upper_token,
)
from app.agent.enhancement.understanding.parser import EnhancedIntentParser
from app.agent.enhancement.understanding.prompts import ENHANCED_INTENT_SYSTEM_PROMPT
from app.agent.intent_parser import IntentParser
from app.agent.intents import (
    REQUIRED_SLOTS,
    Intent,
    IntentDecision,
    ResponseLanguage,
)
from app.agent.llm_client import create_llm_client
from app.agent.prompts import INTENT_SYSTEM_PROMPT
from app.agent.settings import LLMSettings

EXPECTED_CATEGORIES = {
    "standard",
    "colloquial",
    "chinese",
    "english",
    "mixed_language",
    "unknown_intent",
    "missing_slots",
    "dual_intent",
    "too_many_tasks",
    "slot_update",
}
EXPECTED_OUTCOMES = {"new_tasks", "issue_only", "updates_only", "issue_and_updates"}
LEGACY_SHARED_CHECKS = ("intent_sequence", "slots", "missing_slots")


@dataclass(frozen=True)
class ExpectedTask:
    """Ground-truth intent and slots for one task."""

    intent: Intent
    slots: dict[str, Any]
    missing_slots: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationCase:
    """One user message and its expected understanding result."""

    case_id: str
    category: str
    text: str
    language: ResponseLanguage
    tasks: tuple[ExpectedTask, ...]
    issue_types: tuple[str, ...] = ()
    context: ConversationState | None = None
    expected_slot_updates: tuple[SlotUpdate, ...] | None = None
    expected_is_ambiguous: bool | None = None
    expected_outcome: str | None = None


def shared_capability_cases(cases: list[EvaluationCase]) -> list[EvaluationCase]:
    """Select cases that the legacy single-turn, single-task parser can express."""

    return [
        case
        for case in cases
        if case.context is None
        and len(case.tasks) == 1
        and case.expected_slot_updates is None
        and not case.issue_types
        and case.expected_outcome in {None, "new_tasks"}
    ]


def evaluate_legacy_cases(
    parser: IntentParser,
    cases: list[EvaluationCase],
    *,
    model: str,
    provider: str = "unknown",
) -> dict[str, Any]:
    """Adapt the legacy one-decision contract to the common evaluator."""

    class LegacyAdapter:
        def parse(self, text: str, context: ConversationState | None = None):
            if context is not None:
                raise ValueError("Legacy parser does not support conversation context")
            decision = parser.parse(text)
            return UnderstandingResult(
                decisions=[decision],
                language=decision.language,
            )

    selected_cases = shared_capability_cases(cases)
    report = evaluate_cases(
        LegacyAdapter(),
        selected_cases,
        model=model,
        provider=provider,
        evaluation_mode="live_legacy" if provider == "openai" else "mock_legacy",
    )
    report["metadata"].update(
        {
            "parser_version": "legacy IntentParser",
            "prompt_version": "INTENT_SYSTEM_PROMPT",
            "prompt_sha256": hashlib.sha256(
                INTENT_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "parser_implementation": "app.agent.intent_parser.IntentParser",
            "enhanced_parser_implementation": (
                "app.agent.enhancement.understanding.parser.EnhancedIntentParser"
            ),
            "enhanced_prompt_sha256": hashlib.sha256(
                ENHANCED_INTENT_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "dataset_sha256": hashlib.sha256(
                json.dumps(
                    [
                        {
                            "id": case.case_id,
                            "text": case.text,
                            "language": case.language.value,
                            "tasks": [
                                {
                                    "intent": task.intent.value,
                                    "slots": task.slots,
                                    "missing_slots": task.missing_slots,
                                }
                                for task in case.tasks
                            ],
                        }
                        for case in selected_cases
                    ],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
        }
    )
    report["metadata"]["comparison_scope"] = "shared_capability"
    report["metadata"]["shared_checks"] = list(LEGACY_SHARED_CHECKS)
    for item in report["results"]:
        item["checks"] = {
            name: item["checks"][name] for name in LEGACY_SHARED_CHECKS
        }
        item["passed"] = all(item["checks"].values())
        item["status"] = "passed" if item["passed"] else "failed"
        shared_errors = [
            error
            for error in item["error_details"]
            if error["category"] in {"intent_error", "intent_order_error"}
            or error["category"].startswith("slot_")
            or error["category"] == "missing_slot_error"
        ]
        item["error_details"] = shared_errors
        item["error_categories"] = sorted(
            {error["category"] for error in shared_errors}
        )
        actual = item["actual"]
        item["actual"] = {
            key: actual[key]
            for key in ("intents", "slots", "missing_slots")
        }
        item["expected"] = {
            key: item["expected"][key]
            for key in ("intents", "slots", "missing_slots")
        }
    results = report["results"]
    passed = sum(item["passed"] for item in results)
    report["summary"]["passed"] = passed
    report["summary"]["failed"] = len(results) - passed
    report["summary"]["exact_match_accuracy"] = (
        passed / len(results) if results else 0.0
    )
    report["summary"]["component_accuracy"] = {
        name: (
            sum(item["checks"][name] for item in results) / len(results)
            if results
            else 0.0
        )
        for name in LEGACY_SHARED_CHECKS
    }
    report["summary"]["error_category_counts"] = dict(
        sorted(
            Counter(
                category
                for item in results
                for category in item["error_categories"]
            ).items()
        )
    )
    report["metadata"]["git_commit"] = _current_git_commit()
    report["metadata"]["git_worktree_dirty"] = _git_worktree_dirty()
    return report


def _current_git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_worktree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} has invalid fields; missing={missing}, extra={extra}")


def _decision_from_json(raw: Any, label: str) -> IntentDecision:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    _require_exact_keys(
        raw,
        {"intent", "slots", "missing_slots", "language"},
        label,
    )
    try:
        intent = Intent(raw["intent"])
        language = ResponseLanguage(raw["language"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} has an invalid intent or language") from exc
    slots = raw["slots"]
    missing = raw["missing_slots"]
    if not isinstance(slots, dict) or not isinstance(missing, list):
        raise ValueError(f"{label} slots must be an object and missing_slots a list")
    if not all(isinstance(slot, str) for slot in missing):
        raise ValueError(f"{label} missing_slots must contain strings")
    return IntentDecision(
        intent=intent,
        slots=copy.deepcopy(slots),
        missing_slots=tuple(sorted(missing)),
        language=language,
    )


def _context_slot_is_valid(intent: Intent, slot: str, value: Any) -> bool:
    normalizers = {
        "order_id": normalize_order_id,
        "stage": normalize_stage,
        "category": normalize_upper_token,
        "pieces": normalize_positive_integer,
        "requested_delivery_date": normalize_explicit_date,
        "top_n": normalize_positive_integer,
    }
    normalizer = normalizers.get(slot)
    if normalizer is None:
        return False
    allowed = {
        Intent.ORDER_STATUS: {"order_id"},
        Intent.PRODUCTION_NORMALITY: {"stage"},
        Intent.MORNING_BRIEFING: {"top_n"},
        Intent.ORDER_FEASIBILITY: {
            "category", "pieces", "requested_delivery_date"
        },
        Intent.UNKNOWN: set(),
    }[intent]
    return slot in allowed and normalizer(value) == value


def _context_from_json(raw: Any, case_id: str) -> ConversationState:
    if not isinstance(raw, dict):
        raise ValueError(f"Context for {case_id} must be an object")
    _require_exact_keys(raw, {"language", "tasks"}, f"Context for {case_id}")
    try:
        language = ResponseLanguage(raw["language"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Context for {case_id} has invalid language") from exc
    raw_tasks = raw["tasks"]
    if not isinstance(raw_tasks, list):
        raise ValueError(f"Context tasks for {case_id} must be a list")

    tasks: list[TaskState] = []
    task_ids: set[str] = set()
    for index, raw_task in enumerate(raw_tasks):
        label = f"Context task {index} for {case_id}"
        if not isinstance(raw_task, dict):
            raise ValueError(f"{label} must be an object")
        _require_exact_keys(raw_task, {"task_id", "decision", "status", "issues"}, label)
        task_id = raw_task["task_id"]
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"{label} has an empty or invalid task_id")
        if task_id in task_ids:
            raise ValueError(f"Context for {case_id} has duplicate task_id {task_id}")
        task_ids.add(task_id)
        decision = _decision_from_json(raw_task["decision"], f"{label} decision")
        try:
            status = TaskStatus(raw_task["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} has invalid status") from exc
        issues_raw = raw_task["issues"]
        if not isinstance(issues_raw, list):
            raise ValueError(f"{label} issues must be a list")
        issues: list[InterpretationIssue] = []
        for issue_index, raw_issue in enumerate(issues_raw):
            issue_label = f"{label} issue {issue_index}"
            if not isinstance(raw_issue, dict):
                raise ValueError(f"{issue_label} must be an object")
            _require_exact_keys(
                raw_issue,
                {
                    "issue_type",
                    "field",
                    "original_value",
                    "suggested_value",
                    "decision_index",
                    "task_id",
                },
                issue_label,
            )
            try:
                issue_type = InterpretationIssueType(raw_issue["issue_type"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{issue_label} has invalid issue_type") from exc
            if raw_issue["field"] is not None and not isinstance(
                raw_issue["field"], str
            ):
                raise ValueError(f"{issue_label} field must be a string or null")
            if raw_issue["task_id"] != task_id:
                raise ValueError(f"{issue_label} task_id must match its owning task")
            if raw_issue["decision_index"] is not None:
                raise ValueError(
                    f"{issue_label} decision_index must be null in persisted context"
                )
            issues.append(
                InterpretationIssue(
                    issue_type=issue_type,
                    field=raw_issue["field"],
                    original_value=copy.deepcopy(raw_issue["original_value"]),
                    suggested_value=copy.deepcopy(raw_issue["suggested_value"]),
                    decision_index=None,
                    task_id=task_id,
                )
            )

        allowed_slots = {
            Intent.ORDER_STATUS: {"order_id"},
            Intent.PRODUCTION_NORMALITY: {"stage"},
            Intent.MORNING_BRIEFING: {"top_n"},
            Intent.ORDER_FEASIBILITY: {
                "category", "pieces", "requested_delivery_date"
            },
            Intent.UNKNOWN: set(),
        }[decision.intent]
        if not set(decision.slots) <= allowed_slots:
            raise ValueError(f"{label} decision has slots not allowed for its intent")
        if any(
            not _context_slot_is_valid(decision.intent, slot, value)
            for slot, value in decision.slots.items()
        ):
            raise ValueError(f"{label} decision has invalid or non-canonical slot values")
        required = set(REQUIRED_SLOTS[decision.intent])
        actual_missing = set(decision.missing_slots)
        if not actual_missing <= required or actual_missing & set(decision.slots):
            raise ValueError(f"{label} decision has inconsistent missing_slots")
        inferred_missing = {
            slot
            for slot in required
            if slot not in decision.slots
            or decision.slots[slot] is None
            or (
                isinstance(decision.slots[slot], str)
                and not decision.slots[slot].strip()
            )
        }
        if actual_missing != inferred_missing:
            raise ValueError(f"{label} decision missing_slots do not match its slots")
        is_pending = bool(actual_missing or issues)
        if (status == TaskStatus.PENDING) != is_pending:
            raise ValueError(f"{label} status conflicts with missing slots or issues")
        tasks.append(
            TaskState(
                task_id=task_id,
                decision=decision,
                status=status,
                issues=issues,
            )
        )
    return ConversationState(tasks=tasks, language=language)


def _updates_from_json(raw: Any, case_id: str) -> tuple[SlotUpdate, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"expected_slot_updates for {case_id} must be a list")
    updates: list[SlotUpdate] = []
    seen_entries: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        label = f"Slot update {index} for {case_id}"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object")
        _require_exact_keys(item, {"task_id", "slots"}, label)
        if not isinstance(item["task_id"], str) or not item["task_id"].strip():
            raise ValueError(f"{label} has invalid task_id")
        if not isinstance(item["slots"], dict) or not item["slots"]:
            raise ValueError(f"{label} slots must be a non-empty object")
        for slot in item["slots"]:
            identity = (item["task_id"], slot)
            if identity in seen_entries:
                raise ValueError(f"{label} duplicates an expected task/slot update")
            seen_entries.add(identity)
        updates.append(SlotUpdate(item["task_id"], copy.deepcopy(item["slots"])))
    return tuple(updates)


def _update_entries(updates: tuple[SlotUpdate, ...] | list[SlotUpdate]):
    entries: list[tuple[str, str, str, Any]] = []
    for update in updates:
        for slot, value in update.slots.items():
            canonical_value = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            entries.append((update.task_id, slot, canonical_value, value))
    return entries


def _score_slot_updates(
    expected_updates: tuple[SlotUpdate, ...],
    actual_updates: list[SlotUpdate],
) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
    expected_entries = _update_entries(expected_updates)
    actual_entries = _update_entries(actual_updates)
    expected_counts = Counter(entry[:3] for entry in expected_entries)
    actual_counts = Counter(entry[:3] for entry in actual_entries)
    true_positive = sum((expected_counts & actual_counts).values())
    false_negative = sum((expected_counts - actual_counts).values())
    false_positive = sum((actual_counts - expected_counts).values())
    details: list[dict[str, Any]] = []

    expected_remaining = sorted(expected_entries, key=lambda entry: entry[:3])
    actual_remaining = sorted(actual_entries, key=lambda entry: entry[:3])
    for identity in sorted(set(expected_counts) & set(actual_counts)):
        matched = min(expected_counts[identity], actual_counts[identity])
        for _ in range(matched):
            expected_remaining.remove(
                next(entry for entry in expected_remaining if entry[:3] == identity)
            )
            actual_remaining.remove(
                next(entry for entry in actual_remaining if entry[:3] == identity)
            )
    for identity, actual_count in actual_counts.items():
        duplicate_count = actual_count - max(expected_counts[identity], 1)
        if duplicate_count > 0:
            for _ in range(duplicate_count):
                actual_remaining.remove(
                    next(entry for entry in actual_remaining if entry[:3] == identity)
                )
            details.append(
                {
                    "category": "slot_update_duplicate",
                    "task_id": identity[0],
                    "slot": identity[1],
                    "count": duplicate_count,
                }
            )

    unmatched_expected: list[tuple[str, str, str, Any]] = []
    unmatched_actual: list[tuple[str, str, str, Any]] = []
    while expected_remaining and actual_remaining:
        expected = expected_remaining[0]
        expected_task_id, expected_slot, expected_value, expected_raw = expected
        candidate_index = next(
            (
                index
                for index, actual in enumerate(actual_remaining)
                if actual[1] == expected_slot and actual[2] == expected_value
            ),
            None,
        )
        if candidate_index is not None:
            actual = actual_remaining.pop(candidate_index)
            expected_remaining.pop(0)
            details.append(
                {
                    "category": "slot_update_wrong_task_id",
                    "expected_task_id": expected_task_id,
                    "actual_task_id": actual[0],
                    "slot": expected_slot,
                    "value": expected_raw,
                }
            )
            continue
        candidate_index = next(
            (
                index
                for index, actual in enumerate(actual_remaining)
                if actual[0] == expected_task_id and actual[2] == expected_value
            ),
            None,
        )
        if candidate_index is not None:
            actual = actual_remaining.pop(candidate_index)
            expected_remaining.pop(0)
            details.append(
                {
                    "category": "slot_update_wrong_slot",
                    "task_id": expected_task_id,
                    "expected_slot": expected_slot,
                    "actual_slot": actual[1],
                    "value": expected_raw,
                }
            )
            continue
        candidate_index = next(
            (
                index
                for index, actual in enumerate(actual_remaining)
                if actual[0] == expected_task_id and actual[1] == expected_slot
            ),
            None,
        )
        if candidate_index is not None:
            actual = actual_remaining.pop(candidate_index)
            expected_remaining.pop(0)
            details.append(
                {
                    "category": "slot_update_wrong_value",
                    "task_id": expected_task_id,
                    "slot": expected_slot,
                    "expected": expected_raw,
                    "actual": actual[3],
                }
            )
            continue
        unmatched_expected.append(expected_remaining.pop(0))
    if len(unmatched_expected) == 1 and len(actual_remaining) == 1:
        expected = unmatched_expected[0]
        actual = actual_remaining[0]
        if expected[0] == actual[0] and expected[1] != actual[1]:
            details.append(
                {
                    "category": "slot_update_wrong_slot",
                    "task_id": expected[0],
                    "expected_slot": expected[1],
                    "actual_slot": actual[1],
                    "expected": expected[3],
                    "actual": actual[3],
                }
            )
            unmatched_expected.clear()
            actual_remaining.clear()
    unmatched_expected.extend(expected_remaining)
    unmatched_actual.extend(actual_remaining)
    details.extend(
        {
            "category": "slot_update_missing",
            "task_id": entry[0],
            "slot": entry[1],
            "expected": entry[3],
        }
        for entry in unmatched_expected
    )
    details.extend(
        {
            "category": "slot_update_extra",
            "task_id": entry[0],
            "slot": entry[1],
            "actual": entry[3],
        }
        for entry in unmatched_actual
    )
    return (
        expected_counts == actual_counts,
        _prf_metrics(true_positive, false_positive, false_negative),
        details,
    )


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    """Load and validate a JSON ground-truth dataset."""

    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("Evaluation dataset must be a JSON array")

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            raise ValueError(f"Case {index} must be an object")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Case {index} has an invalid id")
        if case_id in seen_ids:
            raise ValueError(f"Duplicate evaluation case id: {case_id}")
        seen_ids.add(case_id)

        category = raw.get("category")
        if category not in EXPECTED_CATEGORIES:
            raise ValueError(f"Unknown category for {case_id}: {category}")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Case {case_id} has empty text")
        try:
            language = ResponseLanguage(raw.get("expected_language"))
        except ValueError as exc:
            raise ValueError(f"Case {case_id} has invalid language") from exc

        raw_tasks = raw.get("expected_tasks")
        if not isinstance(raw_tasks, list) or len(raw_tasks) > 2:
            raise ValueError(f"Case {case_id} must expect a list of zero to two tasks")
        tasks: list[ExpectedTask] = []
        for task_index, raw_task in enumerate(raw_tasks):
            if not isinstance(raw_task, dict):
                raise ValueError(
                    f"Task {task_index} in {case_id} must be an object"
                )
            try:
                intent = Intent(raw_task.get("intent"))
            except ValueError as exc:
                raise ValueError(
                    f"Task {task_index} in {case_id} has invalid intent"
                ) from exc
            slots = raw_task.get("slots", {})
            missing = raw_task.get("missing_slots", [])
            if not isinstance(slots, dict) or not isinstance(missing, list):
                raise ValueError(f"Task {task_index} in {case_id} is malformed")
            if not all(isinstance(slot, str) for slot in missing):
                raise ValueError(
                    f"Task {task_index} in {case_id} has invalid missing slots"
                )
            tasks.append(
                ExpectedTask(
                    intent=intent,
                    slots=slots,
                    missing_slots=tuple(sorted(missing)),
                )
            )

        issue_types = raw.get("expected_issue_types", [])
        if not isinstance(issue_types, list) or not all(
            isinstance(issue, str) for issue in issue_types
        ):
            raise ValueError(f"Case {case_id} has invalid issue types")
        valid_issue_types = {issue.value for issue in InterpretationIssueType}
        if not set(issue_types) <= valid_issue_types:
            raise ValueError(f"Case {case_id} has unknown issue types")

        context = (
            _context_from_json(raw["context"], case_id)
            if "context" in raw
            else None
        )
        expected_slot_updates = (
            _updates_from_json(raw["expected_slot_updates"], case_id)
            if "expected_slot_updates" in raw
            else None
        )
        expected_is_ambiguous = raw.get("expected_is_ambiguous")
        if "expected_is_ambiguous" in raw and not isinstance(
            expected_is_ambiguous, bool
        ):
            raise ValueError(f"Case {case_id} expected_is_ambiguous must be a bool")
        expected_outcome = raw.get("expected_outcome")
        if expected_outcome is not None and (
            not isinstance(expected_outcome, str)
            or expected_outcome not in EXPECTED_OUTCOMES
        ):
            raise ValueError(f"Case {case_id} has invalid expected_outcome")
        if not tasks and (
            "expected_issue_types" not in raw
            or "expected_slot_updates" not in raw
        ):
            raise ValueError(
                f"Case {case_id} with no expected tasks must explicitly declare issues and updates"
            )
        if expected_outcome is not None and "expected_is_ambiguous" not in raw:
            raise ValueError(
                f"Case {case_id} with expected_outcome must declare expected_is_ambiguous"
            )

        if not tasks:
            if expected_outcome not in {
                "issue_only",
                "updates_only",
                "issue_and_updates",
            }:
                raise ValueError(
                    f"Case {case_id} with no expected tasks needs a valid empty-task outcome"
                )
            has_issues = bool(issue_types)
            has_updates = bool(expected_slot_updates)
            if expected_outcome == "issue_only" and not (has_issues and not has_updates):
                raise ValueError(f"Case {case_id} issue_only outcome is inconsistent")
            if expected_outcome == "updates_only" and not (has_updates and not has_issues):
                raise ValueError(f"Case {case_id} updates_only outcome is inconsistent")
            if expected_outcome == "issue_and_updates" and not (
                has_issues and has_updates
            ):
                raise ValueError(f"Case {case_id} issue_and_updates outcome is inconsistent")
        elif expected_outcome is not None and expected_outcome != "new_tasks":
            raise ValueError(f"Case {case_id} non-empty tasks require new_tasks outcome")
        elif expected_outcome == "new_tasks" and not 1 <= len(tasks) <= 2:
            raise ValueError(f"Case {case_id} new_tasks outcome requires one or two tasks")

        if expected_outcome in {"updates_only", "issue_and_updates"} and context is None:
            raise ValueError(f"Case {case_id} slot updates require context")
        if expected_slot_updates and context is None:
            raise ValueError(f"Case {case_id} expected slot updates require context")
        if context is not None and expected_slot_updates is not None:
            context_tasks_by_id = {task.task_id: task for task in context.tasks}
            context_task_ids = set(context_tasks_by_id)
            if any(update.task_id not in context_task_ids for update in expected_slot_updates):
                raise ValueError(f"Case {case_id} update targets an unknown context task")
            if any(
                context_tasks_by_id[update.task_id].status
                in {TaskStatus.COMPLETED, TaskStatus.FAILED}
                for update in expected_slot_updates
            ):
                raise ValueError(f"Case {case_id} update targets a finished context task")
            context_intents = {
                task_id: task.decision.intent
                for task_id, task in context_tasks_by_id.items()
            }
            allowed_update_slots = {
                Intent.ORDER_STATUS: {"order_id"},
                Intent.PRODUCTION_NORMALITY: {"stage"},
                Intent.MORNING_BRIEFING: {"top_n"},
                Intent.ORDER_FEASIBILITY: {
                    "category", "pieces", "requested_delivery_date"
                },
                Intent.UNKNOWN: set(),
            }
            for update in expected_slot_updates:
                update_intent = context_intents[update.task_id]
                if not set(update.slots) <= allowed_update_slots[update_intent]:
                    raise ValueError(
                        f"Case {case_id} update contains slots not allowed for its task"
                    )
                if any(
                    not _context_slot_is_valid(update_intent, slot, value)
                    for slot, value in update.slots.items()
                ):
                    raise ValueError(
                        f"Case {case_id} update contains invalid or non-canonical values"
                    )

        cases.append(
            EvaluationCase(
                case_id=case_id,
                category=category,
                text=text,
                language=language,
                tasks=tuple(tasks),
                issue_types=tuple(sorted(issue_types)),
                context=context,
                expected_slot_updates=expected_slot_updates,
                expected_is_ambiguous=expected_is_ambiguous,
                expected_outcome=expected_outcome,
            )
        )
    return cases


def _prf_metrics(true_positive: int, false_positive: int, false_negative: int) -> dict[str, Any]:
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _append_error(
    details: list[dict[str, Any]],
    category: str,
    **values: Any,
) -> None:
    details.append({"category": category, **values})


def score_result(case: EvaluationCase, result: UnderstandingResult) -> dict[str, Any]:
    """Compare one parser result with ground truth using exact components."""

    expected_intents = [task.intent.value for task in case.tasks]
    actual_intents = [decision.intent.value for decision in result.decisions]
    expected_slots = [task.slots for task in case.tasks]
    actual_slots = [decision.slots for decision in result.decisions]
    expected_missing = [list(task.missing_slots) for task in case.tasks]
    actual_missing = [list(decision.missing_slots) for decision in result.decisions]
    actual_issue_types = sorted(issue.issue_type.value for issue in result.issues)

    checks = {
        "intent_sequence": actual_intents == expected_intents,
        "language": result.language == case.language,
        "slots": actual_slots == expected_slots,
        "missing_slots": actual_missing == expected_missing,
        "issue_types": actual_issue_types == list(case.issue_types),
    }
    update_metrics = None
    update_errors: list[dict[str, Any]] = []
    if case.expected_slot_updates is not None:
        updates_exact, update_metrics, update_errors = _score_slot_updates(
            case.expected_slot_updates,
            result.slot_updates,
        )
        checks["slot_updates"] = updates_exact
    if case.expected_is_ambiguous is not None:
        checks["is_ambiguous"] = result.is_ambiguous == case.expected_is_ambiguous
    if case.expected_outcome in {"issue_only", "updates_only", "issue_and_updates"}:
        checks["no_new_tasks_for_outcome"] = not result.decisions
    errors: list[dict[str, Any]] = []

    aligned_task_count = max(len(expected_intents), len(actual_intents))
    correct_task_intents = 0
    same_intent_multiset = Counter(expected_intents) == Counter(actual_intents)
    if expected_intents != actual_intents and same_intent_multiset:
        _append_error(errors, "intent_order_error")
    else:
        for task_index in range(aligned_task_count):
            expected_intent = (
                expected_intents[task_index]
                if task_index < len(expected_intents)
                else None
            )
            actual_intent = (
                actual_intents[task_index]
                if task_index < len(actual_intents)
                else None
            )
            if expected_intent == actual_intent and expected_intent is not None:
                correct_task_intents += 1
            elif expected_intent != actual_intent:
                _append_error(
                    errors,
                    "intent_error",
                    task_index=task_index,
                    expected=expected_intent,
                    actual=actual_intent,
                )

    slot_counts = Counter()
    missing_slot_counts = Counter()
    max_tasks = max(len(expected_slots), len(actual_slots))
    for task_index in range(max_tasks):
        expected_task_slots = (
            expected_slots[task_index] if task_index < len(expected_slots) else {}
        )
        actual_task_slots = (
            actual_slots[task_index] if task_index < len(actual_slots) else {}
        )
        for slot in sorted(set(expected_task_slots) | set(actual_task_slots)):
            expected_present = slot in expected_task_slots
            actual_present = slot in actual_task_slots
            if expected_present and actual_present:
                if expected_task_slots[slot] == actual_task_slots[slot]:
                    slot_counts["tp"] += 1
                else:
                    slot_counts["fp"] += 1
                    slot_counts["fn"] += 1
                    _append_error(
                        errors,
                        "slot_value_error",
                        task_index=task_index,
                        slot=slot,
                        expected=expected_task_slots[slot],
                        actual=actual_task_slots[slot],
                    )
            elif expected_present:
                slot_counts["fn"] += 1
                _append_error(
                    errors,
                    "slot_missing",
                    task_index=task_index,
                    slot=slot,
                    expected=expected_task_slots[slot],
                )
            else:
                slot_counts["fp"] += 1
                _append_error(
                    errors,
                    "slot_extra",
                    task_index=task_index,
                    slot=slot,
                    actual=actual_task_slots[slot],
                )

    for task_index in range(max(len(expected_missing), len(actual_missing))):
        expected_task_missing = (
            set(expected_missing[task_index])
            if task_index < len(expected_missing)
            else set()
        )
        actual_task_missing = (
            set(actual_missing[task_index])
            if task_index < len(actual_missing)
            else set()
        )
        missing_slot_counts["tp"] += len(expected_task_missing & actual_task_missing)
        omitted = expected_task_missing - actual_task_missing
        false_positive = actual_task_missing - expected_task_missing
        missing_slot_counts["fn"] += len(omitted)
        missing_slot_counts["fp"] += len(false_positive)
        for slot in sorted(omitted):
            _append_error(
                errors,
                "missing_slot_error",
                direction="omitted",
                task_index=task_index,
                slot=slot,
            )
        for slot in sorted(false_positive):
            _append_error(
                errors,
                "missing_slot_error",
                direction="false_positive",
                task_index=task_index,
                slot=slot,
            )

    expected_issue_counts = Counter(case.issue_types)
    actual_issue_counts = Counter(actual_issue_types)
    issue_type_metrics: dict[str, dict[str, Any]] = {}
    for issue_type in sorted(set(expected_issue_counts) | set(actual_issue_counts)):
        true_positive = min(
            expected_issue_counts[issue_type], actual_issue_counts[issue_type]
        )
        false_positive = max(
            actual_issue_counts[issue_type] - expected_issue_counts[issue_type], 0
        )
        false_negative = max(
            expected_issue_counts[issue_type] - actual_issue_counts[issue_type], 0
        )
        issue_type_metrics[issue_type] = _prf_metrics(
            true_positive,
            false_positive,
            false_negative,
        )
        if false_positive or false_negative:
            _append_error(
                errors,
                "issue_type_error",
                issue_type=issue_type,
                fp=false_positive,
                fn=false_negative,
            )

    if not checks["language"]:
        _append_error(
            errors,
            "language_error",
            expected=case.language.value,
            actual=result.language.value,
        )
    if case.expected_is_ambiguous is not None and not checks["is_ambiguous"]:
        _append_error(
            errors,
            "ambiguity_error",
            expected=case.expected_is_ambiguous,
            actual=result.is_ambiguous,
        )
    errors.extend(update_errors)
    if "no_new_tasks_for_outcome" in checks and not checks["no_new_tasks_for_outcome"]:
        _append_error(
            errors,
            "unexpected_new_tasks",
            actual_intents=actual_intents,
        )

    expected_payload = {
        "language": case.language.value,
        "intents": expected_intents,
        "slots": expected_slots,
        "missing_slots": expected_missing,
        "issue_types": list(case.issue_types),
    }
    actual_payload = {
        "language": result.language.value,
        "intents": actual_intents,
        "slots": actual_slots,
        "missing_slots": actual_missing,
        "issue_types": actual_issue_types,
    }
    if case.expected_slot_updates is not None:
        expected_payload["slot_updates"] = [
            {"task_id": update.task_id, "slots": update.slots}
            for update in case.expected_slot_updates
        ]
        actual_payload["slot_updates"] = [
            {"task_id": update.task_id, "slots": update.slots}
            for update in result.slot_updates
        ]
    if case.expected_is_ambiguous is not None:
        expected_payload["is_ambiguous"] = case.expected_is_ambiguous
        actual_payload["is_ambiguous"] = result.is_ambiguous
    if case.expected_outcome is not None:
        expected_payload["outcome"] = case.expected_outcome

    return {
        "id": case.case_id,
        "category": case.category,
        "text": case.text,
        "passed": all(checks.values()),
        "checks": checks,
        "status": "passed" if all(checks.values()) else "failed",
        "error_categories": sorted({error["category"] for error in errors}),
        "error_details": errors,
        "metrics": {
            "task_intent": {
                "correct": correct_task_intents,
                "denominator": aligned_task_count,
                "accuracy": (
                    correct_task_intents / aligned_task_count
                    if aligned_task_count
                    else 0.0
                ),
            },
            "slots": _prf_metrics(
                slot_counts["tp"], slot_counts["fp"], slot_counts["fn"]
            ),
            "missing_slots": _prf_metrics(
                missing_slot_counts["tp"],
                missing_slot_counts["fp"],
                missing_slot_counts["fn"],
            ),
            "issue_types": issue_type_metrics,
            **({"slot_updates": update_metrics} if update_metrics is not None else {}),
        },
        "expected": expected_payload,
        "actual": actual_payload,
    }


def evaluate_cases(
    parser: EnhancedIntentParser,
    cases: list[EvaluationCase],
    *,
    model: str,
    provider: str = "unknown",
    evaluation_mode: str | None = None,
    dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Run all cases and aggregate component and category accuracy."""

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases):02d}] {case.case_id}", flush=True)
        try:
            context_snapshot = copy.deepcopy(case.context)
            if case.context is None:
                result = parser.parse(case.text)
            else:
                parser_context = copy.deepcopy(case.context)
                parser_context_snapshot = copy.deepcopy(parser_context)
                result = parser.parse(case.text, context=parser_context)
                if parser_context != parser_context_snapshot:
                    raise RuntimeError("Parser mutated its evaluation context")
                if case.context != context_snapshot:
                    raise RuntimeError("Evaluation case context changed during parsing")
        except Exception as exc:
            checks = {
                "intent_sequence": False,
                "language": False,
                "slots": False,
                "missing_slots": False,
                "issue_types": False,
            }
            if case.expected_slot_updates is not None:
                checks["slot_updates"] = False
            if case.expected_is_ambiguous is not None:
                checks["is_ambiguous"] = False
            if case.expected_outcome in {"issue_only", "updates_only", "issue_and_updates"}:
                checks["no_new_tasks_for_outcome"] = False
            expected_update_metrics = (
                _score_slot_updates(case.expected_slot_updates, [])[1]
                if case.expected_slot_updates is not None
                else None
            )
            expected_update_errors = (
                _score_slot_updates(case.expected_slot_updates, [])[2]
                if case.expected_slot_updates is not None
                else []
            )
            expected_payload = {
                "language": case.language.value,
                "intents": [task.intent.value for task in case.tasks],
                "slots": [task.slots for task in case.tasks],
                "missing_slots": [list(task.missing_slots) for task in case.tasks],
                "issue_types": list(case.issue_types),
            }
            actual_payload = {
                "language": None,
                "intents": None,
                "slots": None,
                "missing_slots": None,
                "issue_types": None,
            }
            if case.expected_slot_updates is not None:
                expected_payload["slot_updates"] = [
                    {"task_id": update.task_id, "slots": update.slots}
                    for update in case.expected_slot_updates
                ]
                actual_payload["slot_updates"] = []
            if case.expected_is_ambiguous is not None:
                expected_payload["is_ambiguous"] = case.expected_is_ambiguous
                actual_payload["is_ambiguous"] = None
            if case.expected_outcome is not None:
                expected_payload["outcome"] = case.expected_outcome
            exception_error_details = [{"category": "parser_error"}]
            exception_error_details.extend(expected_update_errors)
            exception_error_categories = sorted(
                {detail["category"] for detail in exception_error_details}
            )
            results.append(
                {
                    "id": case.case_id,
                    "category": case.category,
                    "text": case.text,
                    "passed": False,
                    "checks": checks,
                    "status": "error",
                    "error": {
                        "exception_type": type(exc).__name__,
                        "message": "Parser raised an exception; sensitive details omitted.",
                    },
                    "error_categories": exception_error_categories,
                    "error_details": exception_error_details,
                    "expected": expected_payload,
                    "actual": actual_payload,
                    "metrics": {
                        "task_intent": {
                            "correct": 0,
                            "denominator": len(case.tasks),
                            "accuracy": 0.0,
                        },
                        "slots": _prf_metrics(
                            0, 0, sum(len(task.slots) for task in case.tasks)
                        ),
                        "missing_slots": _prf_metrics(
                            0,
                            0,
                            sum(len(task.missing_slots) for task in case.tasks),
                        ),
                        "issue_types": {
                            issue_type: _prf_metrics(0, 0, count)
                            for issue_type, count in Counter(case.issue_types).items()
                        },
                        **(
                            {"slot_updates": expected_update_metrics}
                            if expected_update_metrics is not None
                            else {}
                        ),
                    },
                }
            )
        else:
            results.append(score_result(case, result))

    check_names = (
        "intent_sequence",
        "language",
        "slots",
        "missing_slots",
        "issue_types",
    )
    component_accuracy = {
        name: (
            sum(item["checks"][name] for item in results) / len(results)
            if results
            else 0.0
        )
        for name in check_names
    }
    for optional_check in (
        "slot_updates",
        "is_ambiguous",
        "no_new_tasks_for_outcome",
    ):
        labeled_results = [
            item for item in results if optional_check in item["checks"]
        ]
        if labeled_results:
            component_accuracy[optional_check] = sum(
                item["checks"][optional_check] for item in labeled_results
            ) / len(labeled_results)
    category_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        category_results[result["category"]].append(result)
    category_accuracy = {
        category: sum(item["passed"] for item in items) / len(items)
        for category, items in sorted(category_results.items())
    }
    passed = sum(result["passed"] for result in results)
    task_intent_correct = sum(
        result["metrics"]["task_intent"]["correct"] for result in results
    )
    task_intent_denominator = sum(
        result["metrics"]["task_intent"]["denominator"] for result in results
    )
    slot_totals = Counter()
    missing_slot_totals = Counter()
    issue_totals: dict[str, Counter] = defaultdict(Counter)
    error_category_counts = Counter()
    ambiguous_correct = 0
    ambiguous_denominator = 0
    slot_update_case_correct = 0
    slot_update_case_denominator = 0
    slot_update_totals = Counter()
    for result in results:
        slot_totals.update(result["metrics"]["slots"])
        missing_slot_totals.update(result["metrics"]["missing_slots"])
        for issue_type, metrics in result["metrics"]["issue_types"].items():
            issue_totals[issue_type].update(metrics)
        error_category_counts.update(
            detail["category"] for detail in result["error_details"]
        )
        if "is_ambiguous" in result["checks"]:
            ambiguous_denominator += 1
            ambiguous_correct += result["checks"]["is_ambiguous"]
        if "slot_updates" in result["checks"]:
            slot_update_case_denominator += 1
            slot_update_case_correct += result["checks"]["slot_updates"]
            slot_update_totals.update(result["metrics"]["slot_updates"])
    aggregate_issue_metrics = {
        issue_type: _prf_metrics(counts["tp"], counts["fp"], counts["fn"])
        for issue_type, counts in sorted(
            {
                issue_type.value: issue_totals.get(issue_type.value, Counter())
                for issue_type in InterpretationIssueType
            }.items()
        )
    }
    slot_metrics = _prf_metrics(
        slot_totals["tp"], slot_totals["fp"], slot_totals["fn"]
    )
    missing_slot_metrics = _prf_metrics(
        missing_slot_totals["tp"],
        missing_slot_totals["fp"],
        missing_slot_totals["fn"],
    )
    aggregate_slot_update_metrics = _prf_metrics(
        slot_update_totals["tp"],
        slot_update_totals["fp"],
        slot_update_totals["fn"],
    )
    resolved_mode = evaluation_mode or {
        "mock": "mock",
        "openai": "live",
    }.get(provider, "unknown")
    return {
        "metadata": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider": provider,
            "evaluation_mode": resolved_mode,
            "model": model,
            "parser_version": "enhanced EnhancedIntentParser",
            "prompt_version": "ENHANCED_INTENT_SYSTEM_PROMPT",
            "prompt_sha256": hashlib.sha256(
                ENHANCED_INTENT_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "git_commit": _current_git_commit(),
            "git_worktree_dirty": _git_worktree_dirty(),
            "dataset_sha256": (
                hashlib.sha256(dataset_path.read_bytes()).hexdigest()
                if dataset_path is not None
                else None
            ),
            "comparison_scope": "enhanced_full_dataset",
            "case_count": len(results),
            "category_counts": dict(
                sorted(Counter(case.category for case in cases).items())
            ),
        },
        "summary": {
            "passed": passed,
            "failed": len(results) - passed,
            "exact_match_accuracy": passed / len(results) if results else 0.0,
            "component_accuracy": component_accuracy,
            "category_exact_match_accuracy": category_accuracy,
            "task_intent_accuracy": {
                "correct": task_intent_correct,
                "denominator": task_intent_denominator,
                "accuracy": (
                    task_intent_correct / task_intent_denominator
                    if task_intent_denominator
                    else 0.0
                ),
            },
            "slot_metrics": slot_metrics,
            "missing_slot_metrics": missing_slot_metrics,
            "issue_type_metrics": aggregate_issue_metrics,
            "error_category_counts": dict(sorted(error_category_counts.items())),
            "slot_update_exact_match_accuracy": {
                "correct": slot_update_case_correct,
                "denominator": slot_update_case_denominator,
                "accuracy": (
                    slot_update_case_correct / slot_update_case_denominator
                    if slot_update_case_denominator
                    else 0.0
                ),
            },
            "slot_update_metrics": aggregate_slot_update_metrics,
            "ambiguity_accuracy": {
                "correct": ambiguous_correct,
                "denominator": ambiguous_denominator,
                "accuracy": (
                    ambiguous_correct / ambiguous_denominator
                    if ambiguous_denominator
                    else 0.0
                ),
            },
        },
        "results": results,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a compact human-readable report with every failed case."""

    metadata = report["metadata"]
    summary = report["summary"]
    title = (
        "# Legacy Parser Shared-Capability Evaluation Report"
        if metadata.get("comparison_scope") == "shared_capability"
        else "# Enhanced NLU Evaluation Report"
    )
    lines = [
        title,
        "",
        f"- Generated: {metadata['generated_at']}",
        f"- Model: `{metadata['model']}`",
        f"- Provider: `{metadata.get('provider', 'unknown')}`",
        f"- Evaluation mode: `{metadata.get('evaluation_mode', 'unknown')}`",
        f"- Cases: {metadata['case_count']}",
        f"- Exact matches: {summary['passed']}/{metadata['case_count']} "
        f"({summary['exact_match_accuracy']:.1%})",
        "",
    ]
    if metadata.get("comparison_scope") == "shared_capability":
        lines.extend(
            [
                "- Comparison scope: shared capabilities only",
                "- Shared checks: "
                + ", ".join(metadata.get("shared_checks", [])),
                "",
            ]
        )
    lines.extend([
        "## Component Accuracy",
        "",
        "| Component | Accuracy |",
        "| --- | ---: |",
    ])
    for name, accuracy in summary["component_accuracy"].items():
        lines.append(f"| {name} | {accuracy:.1%} |")

    task_intent = summary.get("task_intent_accuracy", {})
    lines.extend(
        [
            "",
            "## Task-Level Metrics",
            "",
            f"- Task intent accuracy: {task_intent.get('correct', 0)}/"
            f"{task_intent.get('denominator', 0)} "
            f"({task_intent.get('accuracy', 0.0):.1%})",
            f"- Slot precision / recall / F1: "
            f"{summary.get('slot_metrics', {}).get('precision', 0.0):.1%} / "
            f"{summary.get('slot_metrics', {}).get('recall', 0.0):.1%} / "
            f"{summary.get('slot_metrics', {}).get('f1', 0.0):.1%}",
            f"- Missing-slot precision / recall / F1: "
            f"{summary.get('missing_slot_metrics', {}).get('precision', 0.0):.1%} / "
            f"{summary.get('missing_slot_metrics', {}).get('recall', 0.0):.1%} / "
            f"{summary.get('missing_slot_metrics', {}).get('f1', 0.0):.1%}",
            f"- Slot-update exact cases: "
            f"{summary.get('slot_update_exact_match_accuracy', {}).get('correct', 0)}/"
            f"{summary.get('slot_update_exact_match_accuracy', {}).get('denominator', 0)} "
            f"({summary.get('slot_update_exact_match_accuracy', {}).get('accuracy', 0.0):.1%})",
            f"- Slot-update precision / recall / F1: "
            f"{summary.get('slot_update_metrics', {}).get('precision', 0.0):.1%} / "
            f"{summary.get('slot_update_metrics', {}).get('recall', 0.0):.1%} / "
            f"{summary.get('slot_update_metrics', {}).get('f1', 0.0):.1%}",
            f"- Ambiguity accuracy: "
            f"{summary.get('ambiguity_accuracy', {}).get('correct', 0)}/"
            f"{summary.get('ambiguity_accuracy', {}).get('denominator', 0)} "
            f"({summary.get('ambiguity_accuracy', {}).get('accuracy', 0.0):.1%})",
            "",
            "## Error Categories",
            "",
        ]
    )
    error_counts = summary.get("error_category_counts", {})
    if error_counts:
        lines.extend(f"- {category}: {count}" for category, count in error_counts.items())
    else:
        lines.append("No categorized errors.")

    lines.extend(
        [
            "",
            "## Accuracy by Category",
            "",
            "| Category | Cases | Exact accuracy |",
            "| --- | ---: | ---: |",
        ]
    )
    counts = metadata["category_counts"]
    for category, accuracy in summary["category_exact_match_accuracy"].items():
        lines.append(f"| {category} | {counts[category]} | {accuracy:.1%} |")

    failures = [result for result in report["results"] if not result["passed"]]
    lines.extend(["", "## Failed Cases", ""])
    if not failures:
        lines.append("No failed cases.")
    for result in failures:
        failed_checks = [
            name for name, passed in result["checks"].items() if not passed
        ]
        lines.extend(
            [
                f"### {result['id']} — {result['category']}",
                "",
                f"Input: `{result['text']}`",
                "",
                f"Status: `{result.get('status', 'failed')}`",
                "",
                f"Failed checks: {', '.join(failed_checks)}",
                f"Error categories: {', '.join(result.get('error_categories', [])) or 'none'}",
                "",
                "```json",
                json.dumps(
                    {
                        "expected": result["expected"],
                        "actual": result["actual"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_reports(
    report: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
    overwrite: bool = False,
) -> None:
    """Write machine-readable and human-readable evaluation reports."""

    existing_paths = [path for path in (json_path, markdown_path) if path.exists()]
    if existing_paths and not overwrite:
        paths = ", ".join(str(path) for path in existing_paths)
        raise FileExistsError(
            f"Evaluation report already exists: {paths}. Pass overwrite=True to replace it."
        )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")


def main() -> int:
    """Run the checked-in dataset against the configured OpenAI model."""

    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/enhancement/understanding/intent_cases.json"),
    )
    argument_parser.add_argument(
        "--json-report",
        type=Path,
        default=Path("evaluation/nlu_evaluation_report.json"),
    )
    argument_parser.add_argument(
        "--markdown-report",
        type=Path,
        default=Path("evaluation/nlu_evaluation_report.md"),
    )
    argument_parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N cases for a connection or smoke check",
    )
    argument_parser.add_argument(
        "--compare-legacy",
        action="store_true",
        help="Also evaluate the legacy parser on shared-capability cases only",
    )
    argument_parser.add_argument(
        "--legacy-json-report",
        type=Path,
        default=Path("evaluation/nlu_legacy_evaluation_report.json"),
    )
    argument_parser.add_argument(
        "--legacy-markdown-report",
        type=Path,
        default=Path("evaluation/nlu_legacy_evaluation_report.md"),
    )
    argument_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing report files (default: refuse to overwrite)",
    )
    args = argument_parser.parse_args()

    report_paths = [args.json_report, args.markdown_report]
    if args.compare_legacy:
        report_paths.extend([args.legacy_json_report, args.legacy_markdown_report])
    normalized_report_paths = [path.resolve() for path in report_paths]
    if len(set(normalized_report_paths)) != len(normalized_report_paths):
        raise SystemExit("Each requested report must use a distinct output path.")

    if not args.overwrite:
        existing_reports = [
            path
            for path in report_paths
            if path.exists()
        ]
        if existing_reports:
            paths = ", ".join(str(path) for path in existing_reports)
            raise SystemExit(
                f"Evaluation report already exists: {paths}. "
                "Choose new output paths or pass --overwrite."
            )

    settings = LLMSettings.from_env()
    if settings.provider != "openai":
        raise SystemExit("Set MODEL_PROVIDER=openai to run live NLU evaluation")
    cases = load_evaluation_cases(args.dataset)
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit must be greater than zero")
        cases = cases[: args.limit]
    client = create_llm_client(settings)
    parser = EnhancedIntentParser(client)
    report = evaluate_cases(
        parser,
        cases,
        model=settings.model,
        provider=settings.provider,
        dataset_path=args.dataset,
    )
    legacy_report = None
    if args.compare_legacy:
        legacy_cases = shared_capability_cases(cases)
        if args.limit is not None:
            legacy_cases = legacy_cases[: args.limit]
        legacy_report = evaluate_legacy_cases(
            IntentParser(client),
            legacy_cases,
            model=settings.model,
            provider=settings.provider,
        )
    write_reports(
        report,
        json_path=args.json_report,
        markdown_path=args.markdown_report,
        overwrite=args.overwrite,
    )
    print(render_markdown_report(report))
    if legacy_report is not None:
        write_reports(
            legacy_report,
            json_path=args.legacy_json_report,
            markdown_path=args.legacy_markdown_report,
            overwrite=args.overwrite,
        )
        print(render_markdown_report(legacy_report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
