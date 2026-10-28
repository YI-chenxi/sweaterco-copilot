"""Tests for the checked-in NLU evaluation corpus and scorer."""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

import app.agent.enhancement.understanding.evaluation as evaluation_module
from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    SlotUpdate,
    TaskState,
    TaskStatus,
    UnderstandingResult,
)
from app.agent.enhancement.understanding.evaluation import (
    EXPECTED_CATEGORIES,
    EvaluationCase,
    ExpectedTask,
    evaluate_cases,
    evaluate_legacy_cases,
    load_evaluation_cases,
    render_markdown_report,
    score_result,
    shared_capability_cases,
    write_reports,
)
from app.agent.intents import (
    REQUIRED_SLOTS,
    Intent,
    IntentDecision,
    ResponseLanguage,
)

DATASET = Path("tests/enhancement/understanding/intent_cases.json")
EXPECTED_CATEGORY_COUNTS = {
    "standard": 8,
    "colloquial": 7,
    "chinese": 6,
    "english": 6,
    "mixed_language": 6,
    "unknown_intent": 6,
    "missing_slots": 6,
    "dual_intent": 7,
    "too_many_tasks": 1,
    "slot_update": 1,
}
ORIGINAL_CORE_SHA256 = "fe2c2890d4076e7955430a90b88a529ec61f9f3aeacb27786e9346d9a483c788"
NEW_CASES = {
    "eval-049": {
        "category": "colloquial",
        "text": "How's order ODR057 doing?",
        "language": "en",
        "tasks": [(Intent.ORDER_STATUS, {}, ("order_id",))],
        "issues": ("possible_typo",),
    },
    "eval-050": {
        "category": "standard",
        "text": "Can we accept 0 TOPS pieces by 2026-04-25?",

        "language": "en",
        "tasks": [
            (
                Intent.ORDER_FEASIBILITY,
                {"category": "TOPS", "requested_delivery_date": "2026-04-25"},
                ("pieces",),
            )
        ],
        "issues": ("invalid_format",),
    },
    "eval-051": {
        "category": "standard",
        "text": "Can we accept 800 TOPS pieces due 2026-02-30?",
        "language": "en",
        "tasks": [
            (
                Intent.ORDER_FEASIBILITY,
                {"category": "TOPS", "pieces": 800},
                ("requested_delivery_date",),
            )
        ],
        "issues": ("invalid_format",),
    },
    "eval-052": {
        "category": "dual_intent",
        "text": "Check ORD-057, and can we accept 800 TOPS pieces?",
        "language": "en",
        "tasks": [
            (Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()),
            (
                Intent.ORDER_FEASIBILITY,
                {"category": "TOPS", "pieces": 800},
                ("requested_delivery_date",),
            ),
        ],
        "issues": (),
    },
}
ALLOWED_SLOTS = {
    Intent.ORDER_STATUS: {"order_id"},
    Intent.PRODUCTION_NORMALITY: {"stage"},
    Intent.MORNING_BRIEFING: {"top_n"},
    Intent.ORDER_FEASIBILITY: {
        "category",
        "pieces",
        "requested_delivery_date",
    },
    Intent.UNKNOWN: set(),
}


def test_dataset_contains_54_cases_with_expected_category_distribution():
    cases = load_evaluation_cases(DATASET)

    assert len(cases) == 54
    assert Counter(case.category for case in cases) == EXPECTED_CATEGORY_COUNTS
    assert set(EXPECTED_CATEGORY_COUNTS) == EXPECTED_CATEGORIES


def test_legacy_comparison_selects_only_shared_single_turn_cases():
    cases = load_evaluation_cases(DATASET)
    selected = shared_capability_cases(cases)

    assert len(selected) == 40
    assert all(
        case.context is None
        and len(case.tasks) == 1
        and not case.issue_types
        and case.expected_slot_updates is None
        for case in selected
    )


def test_legacy_comparison_uses_common_intent_slot_and_missing_slot_scorer():
    case = _case((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()))

    class LegacyParser:
        def parse(self, _text):
            return IntentDecision(
                intent=Intent.ORDER_STATUS,
                slots={"order_id": "ORD-057"},
                language=ResponseLanguage.ENGLISH,
            )

    report = evaluate_legacy_cases(
        LegacyParser(), [case], model="unit-test", provider="mock"
    )

    assert report["results"][0]["passed"] is True
    assert report["metadata"]["evaluation_mode"] == "mock_legacy"
    assert report["metadata"]["prompt_sha256"]
    assert report["metadata"]["dataset_sha256"]
    assert report["metadata"]["git_commit"]
    assert report["metadata"]["shared_checks"] == [
        "intent_sequence",
        "slots",
        "missing_slots",
    ]
    assert set(report["results"][0]["checks"]) == set(
        report["metadata"]["shared_checks"]
    )


def test_legacy_report_contains_only_shared_capability_fields():
    case = _case((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()))

    class LegacyParser:
        def parse(self, _text):
            return IntentDecision(
                intent=Intent.ORDER_STATUS,
                slots={"order_id": "ORD-057"},
                language=ResponseLanguage.CHINESE,
            )

    report = evaluate_legacy_cases(
        LegacyParser(), [case], model="unit-test", provider="mock"
    )

    assert report["results"][0]["passed"] is True
    assert "enhanced_only_checks" not in report["results"][0]
    assert "enhanced_only_actual" not in report["results"][0]
    assert set(report["results"][0]["actual"]) == {
        "intents",
        "slots",
        "missing_slots",
    }
    assert set(report["results"][0]["expected"]) == {
        "intents",
        "slots",
        "missing_slots",
    }
    assert report["summary"]["component_accuracy"] == {
        "intent_sequence": 1.0,
        "slots": 1.0,
        "missing_slots": 1.0,
    }
    assert "enhanced_only_accuracy" not in report["summary"]


def test_dataset_ids_are_unique():
    cases = load_evaluation_cases(DATASET)

    assert len({case.case_id for case in cases}) == len(cases)


def test_every_case_explicitly_declares_valid_issue_types():
    raw_cases = json.loads(DATASET.read_text(encoding="utf-8"))
    valid_issue_types = {issue.value for issue in InterpretationIssueType}

    assert all("expected_issue_types" in case for case in raw_cases)
    assert all(
        isinstance(case["expected_issue_types"], list)
        and all(issue in valid_issue_types for issue in case["expected_issue_types"])
        for case in raw_cases
    )


def test_all_expected_tasks_use_valid_intents_and_contract_slots():
    cases = load_evaluation_cases(DATASET)

    for case in cases:
        for task in case.tasks:
            required = REQUIRED_SLOTS[task.intent]
            slot_names = set(task.slots)
            missing_slots = set(task.missing_slots)

            assert isinstance(task.intent, Intent)
            assert slot_names <= ALLOWED_SLOTS[task.intent]
            assert missing_slots <= required
            assert missing_slots.isdisjoint(slot_names)
            assert required - slot_names == missing_slots


def test_original_48_core_annotations_are_unchanged():
    raw_cases = json.loads(DATASET.read_text(encoding="utf-8"))
    original_core = [
        {
            key: case[key]
            for key in (
                "id",
                "category",
                "text",
                "expected_language",
                "expected_tasks",
            )
        }
        for case in raw_cases[:48]
    ]
    serialized = json.dumps(
        original_core,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert len(original_core) == 48
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == (
        ORIGINAL_CORE_SHA256
    )


def test_four_new_cases_match_reviewed_ground_truth():
    cases = {case.case_id: case for case in load_evaluation_cases(DATASET)}

    for case_id, expected in NEW_CASES.items():
        case = cases[case_id]
        assert case.category == expected["category"]
        assert case.text == expected["text"]
        assert case.language.value == expected["language"]
        assert [
            (task.intent, task.slots, task.missing_slots) for task in case.tasks
        ] == expected["tasks"]
        assert case.issue_types == expected["issues"]


def test_score_result_requires_every_component_to_match():
    case = EvaluationCase(
        case_id="example",
        category="standard",
        text="How is ORD-057?",
        language=ResponseLanguage.ENGLISH,
        tasks=(
            ExpectedTask(
                intent=Intent.ORDER_STATUS,
                slots={"order_id": "ORD-057"},
                missing_slots=(),
            ),
        ),
    )
    result = UnderstandingResult(
        decisions=[
            IntentDecision(
                intent=Intent.ORDER_STATUS,
                slots={"order_id": "ORD-057"},
                language=ResponseLanguage.ENGLISH,
            )
        ],
        language=ResponseLanguage.ENGLISH,
    )

    score = score_result(case, result)

    assert score["passed"] is True
    assert all(score["checks"].values())


def test_markdown_report_lists_failed_case():
    report = {
        "metadata": {
            "generated_at": "2026-10-27T10:00:00+08:00",
            "model": "test-model",
            "case_count": 1,
            "category_counts": {"standard": 1},
        },
        "summary": {
            "passed": 0,
            "failed": 1,
            "exact_match_accuracy": 0.0,
            "component_accuracy": {"intent_sequence": 0.0},
            "category_exact_match_accuracy": {"standard": 0.0},
        },
        "results": [
            {
                "id": "standard-01",
                "category": "standard",
                "text": "How is ORD-057?",
                "passed": False,
                "checks": {"intent_sequence": False},
                "expected": {"intents": ["order_status"]},
                "actual": {"intents": ["unknown"]},
            }
        ],
    }

    markdown = render_markdown_report(report)

    assert "standard-01" in markdown
    assert "intent_sequence" in markdown


def _case(
    *tasks,
    issue_types=(),
    context=None,
    expected_slot_updates=None,
    expected_is_ambiguous=None,
    expected_outcome=None,
):
    return EvaluationCase(
        case_id="metric-case",
        category="standard",
        text="test input",
        language=ResponseLanguage.ENGLISH,
        tasks=tuple(
            ExpectedTask(intent=intent, slots=slots, missing_slots=missing)
            for intent, slots, missing in tasks
        ),
        issue_types=issue_types,
        context=context,
        expected_slot_updates=expected_slot_updates,
        expected_is_ambiguous=expected_is_ambiguous,
        expected_outcome=expected_outcome,
    )


def _result(*decisions, issues=()):
    return UnderstandingResult(
        decisions=[
            IntentDecision(intent=intent, slots=slots, missing_slots=missing)
            for intent, slots, missing in decisions
        ],
        language=ResponseLanguage.ENGLISH,
        issues=list(issues),
    )


def _pending_feasibility_context(task_id="task-101"):
    return ConversationState(
        language=ResponseLanguage.ENGLISH,
        tasks=[
            TaskState(
                task_id=task_id,
                decision=IntentDecision(
                    intent=Intent.ORDER_FEASIBILITY,
                    slots={"category": "TOPS", "pieces": 800},
                    missing_slots=("requested_delivery_date",),
                    language=ResponseLanguage.ENGLISH,
                ),
                status=TaskStatus.PENDING,
            )
        ],
    )


def _context_json(*, task_id="task-101", slots=None, missing_slots=None, issues=None):
    return {
        "language": "en",
        "tasks": [
            {
                "task_id": task_id,
                "decision": {
                    "intent": "order_feasibility",
                    "slots": slots if slots is not None else {"category": "TOPS", "pieces": 800},
                    "missing_slots": (
                        missing_slots
                        if missing_slots is not None
                        else ["requested_delivery_date"]
                    ),
                    "language": "en",
                },
                "status": "pending",
                "issues": issues if issues is not None else [],
            }
        ],
    }


class _SequenceParser:
    def __init__(self, outputs):
        self._outputs = iter(outputs)

    def parse(self, _text, context=None):
        output = next(self._outputs)
        if isinstance(output, Exception):
            raise output
        if callable(output):
            return output(context)
        return output


def test_task_and_slot_metrics_for_correct_single_task():
    case = _case((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()))

    score = score_result(case, _result((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ())))

    assert score["passed"] is True
    assert score["metrics"]["task_intent"] == {
        "correct": 1,
        "denominator": 1,
        "accuracy": 1.0,
    }
    assert score["metrics"]["slots"]["tp"] == 1
    assert score["metrics"]["slots"]["fp"] == 0
    assert score["metrics"]["slots"]["fn"] == 0


def test_task_order_error_uses_position_alignment():
    case = _case(
        (Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()),
        (Intent.PRODUCTION_NORMALITY, {"stage": "ASSEMBLY"}, ()),
    )
    result = _result(
        (Intent.PRODUCTION_NORMALITY, {"stage": "ASSEMBLY"}, ()),
        (Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()),
    )

    score = score_result(case, result)

    assert score["checks"]["intent_sequence"] is False
    assert score["metrics"]["task_intent"]["denominator"] == 2
    assert score["metrics"]["task_intent"]["accuracy"] == 0.0
    assert "intent_order_error" in score["error_categories"]


@pytest.mark.parametrize(
    ("actual", "expected_accuracy"),
    [
        ([(Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ())], 0.5),
        (
            [
                (Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()),
                (Intent.PRODUCTION_NORMALITY, {"stage": "ASSEMBLY"}, ()),
                (Intent.MORNING_BRIEFING, {}, ()),
            ],
            2 / 3,
        ),
    ],
)
def test_task_count_mismatch_is_included_in_task_intent_denominator(
    actual, expected_accuracy
):
    case = _case(
        (Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()),
        (Intent.PRODUCTION_NORMALITY, {"stage": "ASSEMBLY"}, ()),
    )

    score = score_result(case, _result(*actual))

    assert score["metrics"]["task_intent"]["accuracy"] == expected_accuracy
    assert "intent_error" in score["error_categories"]


@pytest.mark.parametrize(
    ("expected_slots", "actual_slots", "error", "counts"),
    [
        ({"order_id": "ORD-057"}, {}, "slot_missing", (0, 0, 1)),
        ({}, {"order_id": "ORD-057"}, "slot_extra", (0, 1, 0)),
        (
            {"order_id": "ORD-057"},
            {"order_id": "ORD-008"},
            "slot_value_error",
            (0, 1, 1),
        ),
    ],
)
def test_slot_metrics_classify_missing_extra_and_wrong_values(
    expected_slots, actual_slots, error, counts
):
    score = score_result(
        _case((Intent.ORDER_STATUS, expected_slots, ())),
        _result((Intent.ORDER_STATUS, actual_slots, ())),
    )

    metrics = score["metrics"]["slots"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == counts
    assert error in score["error_categories"]


@pytest.mark.parametrize(
    ("expected_missing", "actual_missing", "direction", "counts"),
    [
        (("order_id",), (), "omitted", (0, 0, 1)),
        ((), ("order_id",), "false_positive", (0, 1, 0)),
    ],
)
def test_missing_slot_metrics_distinguish_omission_and_false_positive(
    expected_missing, actual_missing, direction, counts
):
    score = score_result(
        _case((Intent.ORDER_STATUS, {}, expected_missing)),
        _result((Intent.ORDER_STATUS, {}, actual_missing)),
    )

    metrics = score["metrics"]["missing_slots"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == counts
    assert any(
        detail["category"] == "missing_slot_error"
        and detail["direction"] == direction
        for detail in score["error_details"]
    )


@pytest.mark.parametrize(
    ("expected_type", "actual_type", "counts"),
    [
        (InterpretationIssueType.POSSIBLE_TYPO, None, (0, 0, 1)),
        (None, InterpretationIssueType.INVALID_FORMAT, (0, 1, 0)),
    ],
)
def test_issue_type_metrics_record_false_negative_and_false_positive(
    expected_type, actual_type, counts
):
    case = _case(
        (Intent.ORDER_STATUS, {}, ()),
        issue_types=(expected_type.value,) if expected_type else (),
    )
    issues = [InterpretationIssue(issue_type=actual_type)] if actual_type else []
    score = score_result(
        case,
        _result((Intent.ORDER_STATUS, {}, ()), issues=issues),
    )

    issue_type = expected_type or actual_type
    metrics = score["metrics"]["issue_types"][issue_type.value]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == counts
    assert "issue_type_error" in score["error_categories"]


def test_summary_aggregates_issue_metrics_for_all_contract_issue_types():
    case = _case(
        (Intent.ORDER_STATUS, {}, ()),
        issue_types=(InterpretationIssueType.POSSIBLE_TYPO.value,),
    )
    result = _result(
        (Intent.ORDER_STATUS, {}, ()),
        issues=[
            InterpretationIssue(
                issue_type=InterpretationIssueType.POSSIBLE_TYPO
            )
        ],
    )

    report = evaluate_cases(
        _SequenceParser([result]),
        [case],
        model="unit-test",
        provider="mock",
    )

    metrics = report["summary"]["issue_type_metrics"]
    assert set(metrics) == {
        issue.value for issue in InterpretationIssueType
    }
    assert metrics["possible_typo"]["tp"] == 1
    assert metrics["invalid_format"]["tp"] == 0


def test_evaluate_cases_records_parser_exception_and_continues():
    cases = [
        _case((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ())),
        EvaluationCase(
            case_id="second",
            category="standard",
            text="second input",
            language=ResponseLanguage.ENGLISH,
            tasks=(ExpectedTask(Intent.ORDER_STATUS, {}, ("order_id",)),),
        ),
    ]
    parser = _SequenceParser(
        [
            RuntimeError("sensitive exception details"),
            _result((Intent.ORDER_STATUS, {}, ("order_id",))),
        ]
    )

    report = evaluate_cases(parser, cases, model="unit-test", provider="mock")

    assert report["summary"]["failed"] == 1
    assert report["results"][0]["status"] == "error"
    assert report["results"][0]["error_categories"] == ["parser_error"]
    assert "sensitive exception details" not in json.dumps(report)
    assert report["results"][1]["passed"] is True
    assert report["summary"]["error_category_counts"] == {"parser_error": 1}


def test_empty_dataset_returns_zero_accuracy_without_division_error():
    report = evaluate_cases(_SequenceParser([]), [], model="unit-test", provider="mock")

    assert report["metadata"]["case_count"] == 0
    assert report["summary"]["exact_match_accuracy"] == 0.0
    assert set(report["summary"]["component_accuracy"].values()) == {0.0}


@pytest.mark.parametrize(
    ("provider", "expected_mode"),
    [("mock", "mock"), ("openai", "live")],
)
def test_metadata_distinguishes_mock_and_live(provider, expected_mode):
    case = _case((Intent.ORDER_STATUS, {}, ("order_id",)))
    result = _result((Intent.ORDER_STATUS, {}, ("order_id",)))

    report = evaluate_cases(
        _SequenceParser([result]),
        [case],
        model="unit-test-model",
        provider=provider,
    )

    assert report["metadata"]["provider"] == provider
    assert report["metadata"]["evaluation_mode"] == expected_mode
    assert report["metadata"]["model"] == "unit-test-model"


def test_report_preserves_legacy_fields_and_adds_new_sections():
    case = _case((Intent.ORDER_STATUS, {}, ("order_id",)))
    report = evaluate_cases(
        _SequenceParser([_result((Intent.ORDER_STATUS, {}, ("order_id",)))]),
        [case],
        model="unit-test",
        provider="mock",
    )

    assert {"metadata", "summary", "results"} <= set(report)
    assert {
        "passed",
        "failed",
        "exact_match_accuracy",
        "component_accuracy",
        "category_exact_match_accuracy",
    } <= set(report["summary"])
    assert {
        "id",
        "category",
        "text",
        "passed",
        "checks",
        "expected",
        "actual",
    } <= set(report["results"][0])
    json.dumps(report)
    markdown = render_markdown_report(report)
    assert "Task-Level Metrics" in markdown
    assert "Error Categories" in markdown


def test_write_reports_refuses_to_overwrite_existing_file(tmp_path):
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    json_path.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_reports({}, json_path=json_path, markdown_path=markdown_path)

    assert json_path.read_text(encoding="utf-8") == "existing"
    assert not markdown_path.exists()


def test_dataset_includes_step_3_2_cases_with_valid_outcomes():
    cases = {case.case_id: case for case in load_evaluation_cases(DATASET)}

    assert len(cases) == 54
    assert cases["eval-053"].expected_outcome == "issue_only"
    assert cases["eval-053"].tasks == ()
    assert cases["eval-053"].issue_types == ("too_many_tasks",)
    assert cases["eval-053"].expected_is_ambiguous is True
    assert cases["eval-053"].expected_slot_updates == ()
    assert cases["eval-054"].expected_outcome == "updates_only"
    assert cases["eval-054"].expected_is_ambiguous is False
    assert cases["eval-054"].context.tasks[0].task_id == "task-101"
    assert cases["eval-054"].context.tasks[0].status == TaskStatus.PENDING


def test_context_loader_constructs_contract_types_and_independent_instances(tmp_path):
    raw_case = {
        "id": "context-valid",
        "category": "slot_update",
        "text": "The TOPS order is due on 2026-04-25.",
        "expected_language": "en",
        "expected_outcome": "updates_only",
        "context": _context_json(),
        "expected_tasks": [],
        "expected_issue_types": [],
        "expected_is_ambiguous": False,
        "expected_slot_updates": [
            {
                "task_id": "task-101",
                "slots": {"requested_delivery_date": "2026-04-25"},
            }
        ],
    }
    path = tmp_path / "context.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    first = load_evaluation_cases(path)[0]
    second = load_evaluation_cases(path)[0]

    assert isinstance(first.context, ConversationState)
    assert isinstance(first.context.tasks[0], TaskState)
    assert isinstance(first.context.tasks[0].decision, IntentDecision)
    assert first.context.tasks[0].status is TaskStatus.PENDING
    assert first.expected_slot_updates == (
        SlotUpdate(
            task_id="task-101",
            slots={"requested_delivery_date": "2026-04-25"},
        ),
    )
    first.context.tasks[0].decision.slots["pieces"] = 999
    assert second.context.tasks[0].decision.slots["pieces"] == 800


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (_context_json(task_id=" "), "task_id"),
        (
            {
                **_context_json(),
                "tasks": [_context_json()["tasks"][0], _context_json()["tasks"][0]],
            },
            "duplicate task_id",
        ),
        ({**_context_json(), "unknown": True}, "extra"),
        (_context_json(slots={"category": "tops", "pieces": 800}), "non-canonical"),
        (_context_json(missing_slots=[]), "missing_slots"),
        (_context_json()["tasks"][0], "Context for"),
    ],
)
def test_context_loader_rejects_invalid_contract_fixtures(tmp_path, context, message):
    raw_case = {
        "id": "context-invalid",
        "category": "slot_update",
        "text": "Update the order.",
        "expected_language": "en",
        "expected_outcome": "updates_only",
        "context": context,
        "expected_tasks": [],
        "expected_issue_types": [],
        "expected_is_ambiguous": False,
        "expected_slot_updates": [
            {"task_id": "task-101", "slots": {"requested_delivery_date": "2026-04-25"}}
        ],
    }
    path = tmp_path / "invalid-context.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_evaluation_cases(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda context: context["tasks"][0]["decision"].update(
                intent="not-an-intent"
            ),
            "invalid intent or language",
        ),
        (
            lambda context: context["tasks"][0]["decision"].update(language="fr"),
            "invalid intent or language",
        ),
        (
            lambda context: context["tasks"][0].update(status="running"),
            "invalid status",
        ),
        (
            lambda context: context["tasks"][0]["issues"].append(
                {
                    "issue_type": "not-an-issue",
                    "field": "order_id",
                    "original_value": "ODR057",
                    "suggested_value": "ORD-057",
                    "decision_index": None,
                    "task_id": "task-101",
                }
            ),
            "invalid issue_type",
        ),
    ],
)
def test_context_loader_rejects_invalid_contract_enums(tmp_path, mutate, message):
    context = _context_json()
    mutate(context)
    raw_case = {
        "id": "context-invalid-enum",
        "category": "slot_update",
        "text": "The TOPS order is due on 2026-04-25.",
        "expected_language": "en",
        "expected_outcome": "updates_only",
        "context": context,
        "expected_tasks": [],
        "expected_issue_types": [],
        "expected_is_ambiguous": False,
        "expected_slot_updates": [
            {"task_id": "task-101", "slots": {"requested_delivery_date": "2026-04-25"}}
        ],
    }
    path = tmp_path / "invalid-enum.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_evaluation_cases(path)


def test_context_issue_task_id_must_match_owner(tmp_path):
    context = _context_json(
        slots={},
        missing_slots=["order_id"],
        issues=[
            {
                "issue_type": "possible_typo",
                "field": "order_id",
                "original_value": "ODR057",
                "suggested_value": "ORD-057",
                "decision_index": None,
                "task_id": "other-task",
            }
        ],
    )
    context["tasks"][0]["decision"]["intent"] = "order_status"
    raw_case = {
        "id": "issue-owner-invalid",
        "category": "slot_update",
        "text": "Confirm that order.",
        "expected_language": "en",
        "expected_outcome": "updates_only",
        "context": context,
        "expected_tasks": [],
        "expected_issue_types": [],
        "expected_is_ambiguous": False,
        "expected_slot_updates": [
            {"task_id": "task-101", "slots": {"order_id": "ORD-057"}}
        ],
    }
    path = tmp_path / "invalid-issue.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    with pytest.raises(ValueError, match="task_id must match"):
        load_evaluation_cases(path)


@pytest.mark.parametrize(
    ("expected", "actual", "tp_fp_fn", "error"),
    [
        (
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            (1, 0, 0),
            None,
        ),
        (
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            [SlotUpdate("task-999", {"requested_delivery_date": "2026-04-25"})],
            (0, 1, 1),
            "slot_update_wrong_task_id",
        ),
        (
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            [SlotUpdate("task-101", {"pieces": 800})],
            (0, 1, 1),
            "slot_update_wrong_slot",
        ),
        (
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-26"})],
            (0, 1, 1),
            "slot_update_wrong_value",
        ),
        (
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            [],
            (0, 0, 1),
            "slot_update_missing",
        ),
        (
            [],
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            (0, 1, 0),
            "slot_update_extra",
        ),
        (
            [SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})],
            [
                SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"}),
                SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"}),
            ],
            (1, 1, 0),
            "slot_update_duplicate",
        ),
    ],
)
def test_slot_update_metrics_cover_identity_value_and_cardinality(
    expected, actual, tp_fp_fn, error
):
    case = _case(
        context=_pending_feasibility_context(),
        expected_slot_updates=tuple(expected),
        expected_outcome="updates_only",
        expected_is_ambiguous=False,
    )
    result = UnderstandingResult(
        language=ResponseLanguage.ENGLISH,
        slot_updates=actual,
    )

    score = score_result(case, result)

    metrics = score["metrics"]["slot_updates"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == tp_fp_fn
    assert score["checks"]["slot_updates"] is (error is None)
    if error is not None:
        assert error in score["error_categories"]


def test_multiple_slot_updates_are_order_independent():
    expected_updates = (
        SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"}),
        SlotUpdate("task-102", {"order_id": "ORD-057"}),
    )
    actual_updates = [expected_updates[1], expected_updates[0]]
    case = _case(
        context=_pending_feasibility_context(),
        expected_slot_updates=expected_updates,
        expected_outcome="updates_only",
        expected_is_ambiguous=False,
    )

    score = score_result(case, UnderstandingResult(slot_updates=actual_updates))

    assert score["checks"]["slot_updates"] is True
    assert score["metrics"]["slot_updates"]["tp"] == 2


@pytest.mark.parametrize(
    ("expected_ambiguous", "actual_ambiguous"),
    [(True, True), (False, False), (True, False), (False, True)],
)
def test_ambiguity_is_scored_only_when_explicit(expected_ambiguous, actual_ambiguous):
    case = _case(
        expected_is_ambiguous=expected_ambiguous,
        expected_outcome="issue_only",
        issue_types=("ambiguous_reference",),
    )
    result = UnderstandingResult(is_ambiguous=actual_ambiguous)

    score = score_result(case, result)

    assert score["checks"]["is_ambiguous"] is (
        expected_ambiguous == actual_ambiguous
    )


@pytest.mark.parametrize(
    ("case_fields", "message"),
    [
        ({"expected_outcome": "invalid"}, "invalid expected_outcome"),
        ({"expected_outcome": "issue_only"}, "must explicitly declare issues and updates"),
        (
            {
                "expected_outcome": "issue_only",
                "expected_issue_types": ["too_many_tasks"],
                "expected_slot_updates": [],
                "expected_is_ambiguous": True,
            },
            None,
        ),
        (
            {
                "expected_outcome": "updates_only",
                "expected_issue_types": [],
                "expected_slot_updates": [],
                "expected_is_ambiguous": False,
            },
            "updates_only outcome is inconsistent",
        ),
    ],
)
def test_empty_expected_tasks_require_consistent_outcome(
    tmp_path, case_fields, message
):
    raw_case = {
        "id": "empty-outcome",
        "category": "too_many_tasks",
        "text": "Check three things.",
        "expected_language": "en",
        "expected_tasks": [],
        **case_fields,
    }
    path = tmp_path / "empty-outcome.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    if message is None:
        assert load_evaluation_cases(path)[0].tasks == ()
    else:
        with pytest.raises(ValueError, match=message):
            load_evaluation_cases(path)


@pytest.mark.parametrize(
    ("outcome", "expected_issue_types", "updates", "expected_valid"),
    [
        ("issue_only", ["too_many_tasks"], [], True),
        (
            "updates_only",
            [],
            [{"task_id": "task-101", "slots": {"requested_delivery_date": "2026-04-25"}}],
            True,
        ),
        (
            "issue_and_updates",
            ["invalid_format"],
            [{"task_id": "task-101", "slots": {"pieces": 1000}}],
            True,
        ),
        ("new_tasks", ["too_many_tasks"], [], False),
    ],
)
def test_empty_outcome_kinds_are_validated(
    tmp_path, outcome, expected_issue_types, updates, expected_valid
):
    raw_case = {
        "id": f"empty-{outcome}",
        "category": "slot_update" if outcome != "issue_only" else "too_many_tasks",
        "text": "Resolve this request.",
        "expected_language": "en",
        "expected_outcome": outcome,
        "expected_is_ambiguous": outcome != "updates_only",
        "context": _context_json(),
        "expected_tasks": [],
        "expected_issue_types": expected_issue_types,
        "expected_slot_updates": updates,
    }
    path = tmp_path / f"{outcome}.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    if expected_valid:
        assert load_evaluation_cases(path)[0].expected_outcome == outcome
    else:
        with pytest.raises(ValueError, match="needs a valid empty-task outcome"):
            load_evaluation_cases(path)


def test_expected_update_cannot_target_finished_context_task(tmp_path):
    context = _context_json()
    context["tasks"][0]["decision"]["missing_slots"] = []
    context["tasks"][0]["decision"]["slots"]["requested_delivery_date"] = "2026-04-25"
    context["tasks"][0]["status"] = "completed"
    raw_case = {
        "id": "update-completed-task",
        "category": "slot_update",
        "text": "Change that delivery date.",
        "expected_language": "en",
        "expected_outcome": "updates_only",
        "expected_is_ambiguous": False,
        "context": context,
        "expected_tasks": [],
        "expected_issue_types": [],
        "expected_slot_updates": [
            {"task_id": "task-101", "slots": {"requested_delivery_date": "2026-05-01"}}
        ],
    }
    path = tmp_path / "finished-task-update.json"
    path.write_text(json.dumps([raw_case]), encoding="utf-8")

    with pytest.raises(ValueError, match="finished context task"):
        load_evaluation_cases(path)


def test_too_many_tasks_issue_only_accepts_no_partial_decisions():
    case = _case(
        issue_types=(InterpretationIssueType.TOO_MANY_TASKS.value,),
        expected_is_ambiguous=True,
        expected_outcome="issue_only",
    )
    result = UnderstandingResult(
        decisions=[],
        is_ambiguous=True,
        issues=[
            InterpretationIssue(
                issue_type=InterpretationIssueType.TOO_MANY_TASKS
            )
        ],
    )

    score = score_result(case, result)

    assert score["passed"] is True
    assert score["checks"]["no_new_tasks_for_outcome"] is True

    partial_result = _result((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()))
    partial_result.is_ambiguous = True
    partial_result.issues = [
        InterpretationIssue(issue_type=InterpretationIssueType.TOO_MANY_TASKS)
    ]
    partial_score = score_result(case, partial_result)
    assert partial_score["checks"]["no_new_tasks_for_outcome"] is False


def test_updates_only_fails_if_parser_creates_new_business_task():
    case = _case(
        context=_pending_feasibility_context(),
        expected_slot_updates=(
            SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"}),
        ),
        expected_is_ambiguous=False,
        expected_outcome="updates_only",
    )
    result = _result((Intent.ORDER_STATUS, {"order_id": "ORD-057"}, ()))
    result.slot_updates = [
        SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"})
    ]

    score = score_result(case, result)

    assert score["checks"]["slot_updates"] is True
    assert score["checks"]["no_new_tasks_for_outcome"] is False
    assert "unexpected_new_tasks" in score["error_categories"]


def test_evaluator_passes_independent_context_and_detects_parser_mutation():
    context = _pending_feasibility_context()
    case = _case(
        context=context,
        expected_slot_updates=(
            SlotUpdate("task-101", {"requested_delivery_date": "2026-04-25"}),
        ),
        expected_is_ambiguous=False,
        expected_outcome="updates_only",
    )

    def mutating_parser(parser_context):
        assert isinstance(parser_context, ConversationState)
        parser_context.tasks[0].decision.slots["pieces"] = 999
        return UnderstandingResult()

    report = evaluate_cases(
        _SequenceParser([mutating_parser]),
        [case],
        model="test",
        provider="mock",
    )

    assert report["results"][0]["status"] == "error"
    assert context.tasks[0].decision.slots["pieces"] == 800


def test_cli_checks_existing_report_paths_before_loading_client(monkeypatch, tmp_path):
    existing_json = tmp_path / "existing.json"
    existing_json.write_text("existing", encoding="utf-8")
    markdown_path = tmp_path / "new.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--json-report",
            str(existing_json),
            "--markdown-report",
            str(markdown_path),
        ],
    )
    monkeypatch.setattr(
        evaluation_module.LLMSettings,
        "from_env",
        lambda: pytest.fail("settings/client setup must happen after report preflight"),
    )

    with pytest.raises(SystemExit, match="already exists"):
        evaluation_module.main()

    assert not markdown_path.exists()


def test_cli_rejects_duplicate_report_paths_before_loading_client(monkeypatch, tmp_path):
    report_path = tmp_path / "same.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--compare-legacy",
            "--json-report",
            str(report_path),
            "--markdown-report",
            str(tmp_path / "enhanced.md"),
            "--legacy-json-report",
            str(report_path),
            "--legacy-markdown-report",
            str(tmp_path / "legacy.md"),
        ],
    )
    monkeypatch.setattr(
        evaluation_module.LLMSettings,
        "from_env",
        lambda: pytest.fail("settings/client setup must happen after path validation"),
    )

    with pytest.raises(SystemExit, match="distinct output path"):
        evaluation_module.main()

    assert not report_path.exists()
