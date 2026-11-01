"""Ground-truth evaluation for enhanced natural-language understanding."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.enhancement.contracts import UnderstandingResult
from app.agent.enhancement.understanding.parser import EnhancedIntentParser
from app.agent.intents import Intent, ResponseLanguage
from app.agent.llm_client import create_llm_client
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
}


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
        if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 2:
            raise ValueError(f"Case {case_id} must expect one or two tasks")
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
        cases.append(
            EvaluationCase(
                case_id=case_id,
                category=category,
                text=text,
                language=language,
                tasks=tuple(tasks),
                issue_types=tuple(sorted(issue_types)),
            )
        )
    return cases


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
    return {
        "id": case.case_id,
        "category": case.category,
        "text": case.text,
        "passed": all(checks.values()),
        "checks": checks,
        "expected": {
            "language": case.language.value,
            "intents": expected_intents,
            "slots": expected_slots,
            "missing_slots": expected_missing,
            "issue_types": list(case.issue_types),
        },
        "actual": {
            "language": result.language.value,
            "intents": actual_intents,
            "slots": actual_slots,
            "missing_slots": actual_missing,
            "issue_types": actual_issue_types,
        },
    }


def evaluate_cases(
    parser: EnhancedIntentParser,
    cases: list[EvaluationCase],
    *,
    model: str,
) -> dict[str, Any]:
    """Run all cases and aggregate component and category accuracy."""

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases):02d}] {case.case_id}", flush=True)
        result = parser.parse(case.text)
        results.append(score_result(case, result))

    check_names = tuple(results[0]["checks"]) if results else ()
    component_accuracy = {
        name: sum(item["checks"][name] for item in results) / len(results)
        for name in check_names
    }
    category_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        category_results[result["category"]].append(result)
    category_accuracy = {
        category: sum(item["passed"] for item in items) / len(items)
        for category, items in sorted(category_results.items())
    }
    passed = sum(result["passed"] for result in results)
    return {
        "metadata": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model,
            "case_count": len(results),
            "category_counts": dict(
                sorted(Counter(case.category for case in cases).items())
            ),
        },
        "summary": {
            "passed": passed,
            "failed": len(results) - passed,
            "exact_match_accuracy": passed / len(results),
            "component_accuracy": component_accuracy,
            "category_exact_match_accuracy": category_accuracy,
        },
        "results": results,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a compact human-readable report with every failed case."""

    metadata = report["metadata"]
    summary = report["summary"]
    lines = [
        "# Enhanced NLU Evaluation Report",
        "",
        f"- Generated: {metadata['generated_at']}",
        f"- Model: `{metadata['model']}`",
        f"- Cases: {metadata['case_count']}",
        f"- Exact matches: {summary['passed']}/{metadata['case_count']} "
        f"({summary['exact_match_accuracy']:.1%})",
        "",
        "## Component Accuracy",
        "",
        "| Component | Accuracy |",
        "| --- | ---: |",
    ]
    for name, accuracy in summary["component_accuracy"].items():
        lines.append(f"| {name} | {accuracy:.1%} |")

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
                f"Failed checks: {', '.join(failed_checks)}",
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
) -> None:
    """Write machine-readable and human-readable evaluation reports."""

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
        default=Path("evaluation/nlu_cases.json"),
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
    args = argument_parser.parse_args()

    settings = LLMSettings.from_env()
    if settings.provider != "openai":
        raise SystemExit("Set MODEL_PROVIDER=openai to run live NLU evaluation")
    cases = load_evaluation_cases(args.dataset)
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit must be greater than zero")
        cases = cases[: args.limit]
    parser = EnhancedIntentParser(create_llm_client(settings))
    report = evaluate_cases(parser, cases, model=settings.model)
    write_reports(
        report,
        json_path=args.json_report,
        markdown_path=args.markdown_report,
    )
    print(render_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
