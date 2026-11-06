import json

from tests.proactive.evaluate import DEFAULT_CASES, classify_failure, score


def test_exact_prediction_passes_and_accuracy_is_reproducible() -> None:
    cases = [
        {
            "id": "c1",
            "feature": "briefing",
            "category": "create",
            "language": "en",
            "expected": {
                "command_type": "create",
                "task_type": "briefing",
                "parameters": {"interval_days": 3},
                "missing_fields": [],
                "proposal_allowed": True,
            },
        }
    ]
    predictions = {
        "c1": {
            "command_type": "create",
            "task_type": "briefing",
            "parameters": {"interval_days": 3},
            "missing_fields": [],
            "language": "en",
        }
    }
    report = score(cases, predictions)
    assert report["accuracy"] == 1.0
    assert report["by_feature"]["briefing"] == {"total": 1, "passed": 1}
    assert report["by_category"]["create"] == {"total": 1, "passed": 1}
    assert report["metrics"]["proposal_safety_gate"]["accuracy"] == 1.0
    assert report["proposal_gate_confusion"]["true_positive"] == 1
    assert report["proposal_gate_confusion"]["false_positive"] == 0


def test_failure_taxonomy_catches_unbound_confirmation() -> None:
    expected = {
        "command_type": "confirm",
        "task_type": None,
        "target_id": "proposal-id",
        "missing_fields": [],
    }
    actual = {
        "command_type": "confirm",
        "task_type": None,
        "target_id": None,
        "missing_fields": ["target_id"],
    }
    assert classify_failure(expected, actual) == "wrong_or_unbound_target"


def test_missing_prediction_is_a_failure_not_silently_omitted() -> None:
    report = score(
        [
            {
                "id": "missing",
                "feature": "watch",
                "category": "negative",
                "expected": {"command_type": "create", "task_type": "watch"},
            }
        ],
        {},
    )
    assert report["case_count"] == 1
    assert report["passed"] == 0
    assert report["failure_taxonomy"] == {"wrong_command_type": 1}


def test_p1_seed_cases_have_unique_ids_context_and_safety_labels() -> None:
    cases = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert all("context" in case and "proposal_allowed" in case["expected"] for case in cases)
    blocked_categories = {"negative", "unsupported", "safety", "ambiguity", "clarification"}
    assert all(
        case["expected"]["proposal_allowed"] is False
        for case in cases
        if case["category"] in blocked_categories
    )
    assert len(cases) >= 30
    assert sum(case["expected"]["proposal_allowed"] for case in cases) >= 5
    adversarial_categories = {
        "negative",
        "unsupported",
        "safety",
        "no_answer",
        "adversarial_ambiguity",
        "adversarial_invalid_value",
        "adversarial_context",
        "adversarial_unsupported_condition",
    }
    assert sum(case["category"] in adversarial_categories for case in cases) >= 5


def test_complete_command_without_missing_fields_is_the_only_proposal_gate() -> None:
    expected = {
        "command_type": "create",
        "task_type": "briefing",
        "parameters": {"interval_days": 1},
        "target_id": None,
        "missing_fields": [],
        "proposal_allowed": True,
    }
    assert classify_failure(expected, expected) is None
    blocked = {**expected, "missing_fields": ["start_date"]}
    assert classify_failure(expected, blocked) == "clarification_or_missing_field"
