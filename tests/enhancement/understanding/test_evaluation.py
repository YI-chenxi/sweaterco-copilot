"""Tests for the checked-in NLU evaluation corpus and scorer."""

from collections import Counter
from pathlib import Path

from app.agent.enhancement.contracts import UnderstandingResult
from app.agent.enhancement.understanding.evaluation import (
    EXPECTED_CATEGORIES,
    EvaluationCase,
    ExpectedTask,
    load_evaluation_cases,
    render_markdown_report,
    score_result,
)
from app.agent.intents import Intent, IntentDecision, ResponseLanguage

DATASET = Path("evaluation/nlu_cases.json")


def test_dataset_contains_six_cases_for_each_required_category():
    cases = load_evaluation_cases(DATASET)

    assert len(cases) == 48
    assert Counter(case.category for case in cases) == {
        category: 6 for category in EXPECTED_CATEGORIES
    }


def test_dataset_ids_are_unique():
    cases = load_evaluation_cases(DATASET)

    assert len({case.case_id for case in cases}) == len(cases)


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
