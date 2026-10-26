"""Validated bilingual understanding for one or two independent tasks."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    SlotUpdate,
    TaskStatus,
    UnderstandingResult,
)
from app.agent.enhancement.understanding.normalizers import (
    find_order_id_typo_candidates,
    normalize_explicit_date,
    normalize_order_id,
    normalize_order_ids_in_text,
    normalize_positive_integer,
    normalize_stage,
    normalize_upper_token,
)
from app.agent.enhancement.understanding.prompts import (
    ENHANCED_INTENT_RESPONSE_SCHEMA,
    ENHANCED_INTENT_SYSTEM_PROMPT,
)
from app.agent.intent_parser import detect_response_language
from app.agent.intents import (
    Intent,
    IntentDecision,
    ResponseLanguage,
    find_missing_slots,
    required_slots_for,
)
from app.agent.llm_client import LLMClientError, StructuredLLMClient

LOGGER = logging.getLogger(__name__)

_ALLOWED_SLOTS: dict[Intent, frozenset[str]] = {
    Intent.ORDER_STATUS: frozenset({"order_id"}),
    Intent.PRODUCTION_NORMALITY: frozenset({"stage"}),
    Intent.MORNING_BRIEFING: frozenset({"top_n"}),
    Intent.ORDER_FEASIBILITY: frozenset(
        {"category", "pieces", "requested_delivery_date"}
    ),
    Intent.UNKNOWN: frozenset(),
}
_AFFIRMATIVE_REPLIES = {
    "yes",
    "yes it is",
    "correct",
    "that's right",
    "是",
    "是的",
    "对",
    "对的",
    "没错",
}


def _normalize_affirmative_text(text: str) -> str:
    cleaned = re.sub(r"[\s.!?,，。！？]+", " ", text.strip().lower())
    return cleaned.strip()


def _normalizer_for(slot: str):
    return {
        "order_id": normalize_order_id,
        "stage": normalize_stage,
        "category": normalize_upper_token,
        "pieces": normalize_positive_integer,
        "requested_delivery_date": normalize_explicit_date,
        "top_n": normalize_positive_integer,
    }[slot]


def _invalid_value_issue(
    *,
    slot: str,
    raw_value: Any,
    decision_index: int | None = None,
    task_id: str | None = None,
) -> InterpretationIssue:
    typo_candidates = (
        find_order_id_typo_candidates(str(raw_value))
        if slot == "order_id"
        else []
    )
    if len(typo_candidates) == 1:
        original, suggested = typo_candidates[0]
        return InterpretationIssue(
            issue_type=InterpretationIssueType.POSSIBLE_TYPO,
            field=slot,
            original_value=original,
            suggested_value=suggested,
            decision_index=decision_index,
            task_id=task_id,
        )
    return InterpretationIssue(
        issue_type=InterpretationIssueType.INVALID_FORMAT,
        field=slot,
        original_value=raw_value,
        decision_index=decision_index,
        task_id=task_id,
    )


def _decision_from_payload(
    payload: dict[str, Any],
    *,
    language: ResponseLanguage,
    decision_index: int,
) -> tuple[IntentDecision, list[InterpretationIssue]]:
    try:
        intent = Intent(payload.get("intent", Intent.UNKNOWN.value))
    except ValueError:
        intent = Intent.UNKNOWN

    if intent == Intent.UNKNOWN:
        return IntentDecision(intent=intent, language=language), []

    slots: dict[str, Any] = {}
    issues: list[InterpretationIssue] = []
    for slot in _ALLOWED_SLOTS[intent]:
        raw_value = payload.get(slot)
        if raw_value is None:
            continue
        normalized = _normalizer_for(slot)(raw_value)
        if normalized is None:
            issues.append(
                _invalid_value_issue(
                    slot=slot,
                    raw_value=raw_value,
                    decision_index=decision_index,
                )
            )
            continue
        slots[slot] = normalized

    declared_missing = payload.get("missing_slots", [])
    if not isinstance(declared_missing, list):
        declared_missing = []
    required = required_slots_for(intent)
    missing = {
        slot
        for slot in declared_missing
        if isinstance(slot, str) and slot in required
    }
    decision = IntentDecision(
        intent=intent,
        slots=slots,
        missing_slots=tuple(sorted(missing)),
        language=language,
    )
    decision = replace(decision, missing_slots=find_missing_slots(decision))
    return decision, issues


def _issue_from_payload(
    payload: dict[str, Any],
    *,
    decision_count: int,
) -> InterpretationIssue | None:
    try:
        issue_type = InterpretationIssueType(payload.get("issue_type"))
    except (TypeError, ValueError):
        return None

    decision_index = payload.get("decision_index")
    if not isinstance(decision_index, int) or isinstance(decision_index, bool):
        decision_index = None
    elif not 0 <= decision_index < decision_count:
        decision_index = None

    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        task_id = None

    field = payload.get("field")
    if not isinstance(field, str) or not field.strip():
        field = None

    return InterpretationIssue(
        issue_type=issue_type,
        field=field,
        original_value=payload.get("original_value"),
        suggested_value=payload.get("suggested_value"),
        decision_index=decision_index,
        task_id=task_id,
    )


def _updates_from_payload(
    payloads: list[Any],
    *,
    context: ConversationState | None,
) -> tuple[list[SlotUpdate], list[InterpretationIssue]]:
    updates: list[SlotUpdate] = []
    issues: list[InterpretationIssue] = []
    tasks_by_id = {
        task.task_id: task
        for task in (context.tasks if context is not None else [])
    }

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or task_id not in tasks_by_id:
            issues.append(
                InterpretationIssue(
                    issue_type=InterpretationIssueType.AMBIGUOUS_REFERENCE,
                    original_value=task_id,
                    task_id=task_id if isinstance(task_id, str) else None,
                )
            )
            continue

        task = tasks_by_id[task_id]
        slots: dict[str, Any] = {}
        for slot in _ALLOWED_SLOTS[task.decision.intent]:
            raw_value = payload.get(slot)
            if raw_value is None:
                continue
            normalized = _normalizer_for(slot)(raw_value)
            if normalized is None:
                issues.append(
                    _invalid_value_issue(
                        slot=slot,
                        raw_value=raw_value,
                        task_id=task_id,
                    )
                )
                continue
            slots[slot] = normalized
        if slots:
            updates.append(SlotUpdate(task_id=task_id, slots=slots))

    return updates, issues


def _context_as_json(context: ConversationState | None) -> str:
    if context is None:
        return "[]"
    tasks = [
        {
            "task_id": task.task_id,
            "intent": task.decision.intent.value,
            "slots": task.decision.slots,
            "missing_slots": list(task.decision.missing_slots),
            "status": task.status.value,
            "issues": [
                {
                    "issue_type": issue.issue_type.value,
                    "field": issue.field,
                    "original_value": issue.original_value,
                    "suggested_value": issue.suggested_value,
                }
                for issue in task.issues
            ],
        }
        for task in context.tasks
    ]
    return json.dumps(tasks, ensure_ascii=False, sort_keys=True)


def _affirmative_slot_update(
    user_text: str,
    context: ConversationState | None,
) -> UnderstandingResult | None:
    if context is None:
        return None
    if _normalize_affirmative_text(user_text) not in _AFFIRMATIVE_REPLIES:
        return None

    candidates: list[tuple[Any, InterpretationIssue, Any]] = []
    for task in context.tasks:
        if task.status != TaskStatus.PENDING:
            continue
        for issue in task.issues:
            if (
                issue.issue_type != InterpretationIssueType.POSSIBLE_TYPO
                or issue.field not in _ALLOWED_SLOTS[task.decision.intent]
                or issue.suggested_value is None
            ):
                continue
            normalized_value = _normalizer_for(issue.field)(
                issue.suggested_value
            )
            if normalized_value is not None:
                candidates.append((task, issue, normalized_value))
    if len(candidates) != 1:
        return None

    task, issue, normalized_value = candidates[0]
    return UnderstandingResult(
        language=context.language,
        slot_updates=[
            SlotUpdate(
                task_id=task.task_id,
                slots={issue.field: normalized_value},
            )
        ],
    )


class EnhancedIntentParser:
    """Parse bilingual text into new tasks, slot updates, and issues."""

    def __init__(self, client: StructuredLLMClient) -> None:
        self.client = client

    def parse(
        self,
        user_text: str,
        context: ConversationState | None = None,
    ) -> UnderstandingResult:
        """Return a validated understanding result without executing tools."""

        if not isinstance(user_text, str) or not user_text.strip():
            return UnderstandingResult(
                decisions=[IntentDecision(intent=Intent.UNKNOWN)]
            )

        affirmative = _affirmative_slot_update(user_text, context)
        if affirmative is not None:
            return affirmative

        fallback_language = detect_response_language(user_text)
        normalized_text = normalize_order_ids_in_text(user_text.strip())
        user_prompt = (
            f"User message:\n{normalized_text}\n\n"
            f"Conversation tasks:\n{_context_as_json(context)}"
        )
        try:
            payload = self.client.generate_structured(
                system_prompt=ENHANCED_INTENT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=ENHANCED_INTENT_RESPONSE_SCHEMA,
            )
            if not isinstance(payload, dict):
                raise LLMClientError("Structured LLM output must be an object")
            result = self._result_from_payload(
                payload,
                fallback_language=fallback_language,
                context=context,
            )
        except (LLMClientError, TypeError, ValueError) as exc:
            LOGGER.warning(
                "Enhanced intent parsing failed; using UNKNOWN fallback: %s",
                exc,
            )
            return UnderstandingResult(
                decisions=[
                    IntentDecision(
                        intent=Intent.UNKNOWN,
                        language=fallback_language,
                    )
                ],
                language=fallback_language,
            )

        return self._merge_deterministic_typo_issues(result, user_text)

    def _result_from_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback_language: ResponseLanguage,
        context: ConversationState | None,
    ) -> UnderstandingResult:
        try:
            language = ResponseLanguage(
                payload.get("language", fallback_language.value)
            )
        except ValueError:
            language = fallback_language

        task_payloads = payload.get("tasks", [])
        update_payloads = payload.get("slot_updates", [])
        issue_payloads = payload.get("issues", [])
        if not all(
            isinstance(value, list)
            for value in (task_payloads, update_payloads, issue_payloads)
        ):
            raise LLMClientError("Tasks, slot_updates, and issues must be arrays")

        if len(task_payloads) > 2:
            issue = InterpretationIssue(
                issue_type=InterpretationIssueType.TOO_MANY_TASKS,
                original_value=len(task_payloads),
                suggested_value=2,
            )
            return UnderstandingResult(
                is_ambiguous=True,
                language=language,
                issues=[issue],
            )

        decisions: list[IntentDecision] = []
        issues: list[InterpretationIssue] = []
        for index, task_payload in enumerate(task_payloads):
            if not isinstance(task_payload, dict):
                raise LLMClientError("Every task must be an object")
            decision, task_issues = _decision_from_payload(
                task_payload,
                language=language,
                decision_index=index,
            )
            decisions.append(decision)
            issues.extend(task_issues)

        for issue_payload in issue_payloads:
            if not isinstance(issue_payload, dict):
                continue
            issue = _issue_from_payload(
                issue_payload,
                decision_count=len(decisions),
            )
            if issue is not None:
                issues.append(issue)

        updates, update_issues = _updates_from_payload(
            update_payloads,
            context=context,
        )
        issues.extend(update_issues)

        if not decisions and not updates and not issues:
            decisions.append(IntentDecision(intent=Intent.UNKNOWN, language=language))

        return UnderstandingResult(
            decisions=decisions,
            is_ambiguous=bool(payload.get("is_ambiguous")) or bool(issues),
            language=language,
            issues=issues,
            slot_updates=updates,
        )

    @staticmethod
    def _merge_deterministic_typo_issues(
        result: UnderstandingResult,
        original_text: str,
    ) -> UnderstandingResult:
        candidates = find_order_id_typo_candidates(original_text)
        if not candidates:
            return result

        existing = {
            (issue.original_value, issue.suggested_value)
            for issue in result.issues
            if issue.issue_type == InterpretationIssueType.POSSIBLE_TYPO
        }
        for original, suggested in candidates:
            matching_decisions = [
                index
                for index, decision in enumerate(result.decisions)
                if decision.intent == Intent.ORDER_STATUS
                and decision.slots.get("order_id") == suggested
            ]
            matching_updates = [
                index
                for index, update in enumerate(result.slot_updates)
                if update.slots.get("order_id") == suggested
            ]
            exact_matches = len(matching_decisions) + len(matching_updates)

            decision_index: int | None = None
            task_id: str | None = None
            if exact_matches == 1 and matching_decisions:
                decision_index = matching_decisions[0]
                decision = result.decisions[decision_index]
                slots = dict(decision.slots)
                slots.pop("order_id")
                without_correction = replace(decision, slots=slots)
                result.decisions[decision_index] = replace(
                    without_correction,
                    missing_slots=find_missing_slots(without_correction),
                )
            elif exact_matches == 1 and matching_updates:
                update_index = matching_updates[0]
                update = result.slot_updates[update_index]
                task_id = update.task_id
                slots = dict(update.slots)
                slots.pop("order_id")
                result.slot_updates[update_index] = replace(update, slots=slots)
            else:
                order_indexes = [
                    index
                    for index, decision in enumerate(result.decisions)
                    if decision.intent == Intent.ORDER_STATUS
                ]
                if len(order_indexes) == 1:
                    decision_index = order_indexes[0]

            if (original, suggested) in existing:
                continue
            result.issues.append(
                InterpretationIssue(
                    issue_type=InterpretationIssueType.POSSIBLE_TYPO,
                    field="order_id",
                    original_value=original,
                    suggested_value=suggested,
                    decision_index=decision_index,
                    task_id=task_id,
                )
            )
        result.slot_updates = [
            update for update in result.slot_updates if update.slots
        ]
        result.is_ambiguous = True
        return result
