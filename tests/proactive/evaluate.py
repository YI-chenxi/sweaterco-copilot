"""Reproducible static scoring and failure taxonomy for proactive command cases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ROOT / "cases.json"


def classify_failure(expected: dict[str, Any], actual: dict[str, Any]) -> str | None:
    expected_command = {key: value for key, value in expected.items() if key != "proposal_allowed"}
    if actual.get("command_type") != expected_command.get("command_type"):
        return "wrong_command_type"
    if actual.get("task_type") != expected_command.get("task_type"):
        return "wrong_task_type"
    if actual.get("target_id") != expected_command.get("target_id"):
        return "wrong_or_unbound_target"
    if actual.get("parameters", {}) != expected_command.get("parameters", {}):
        return "parameter_extraction_or_normalization"
    if sorted(actual.get("missing_fields", [])) != sorted(
        expected_command.get("missing_fields", [])
    ):
        return "clarification_or_missing_field"
    proposal_allowed = actual.get("command_type") in {
        "create",
        "update",
        "cancel",
    } and not actual.get("missing_fields", [])
    if proposal_allowed != expected.get("proposal_allowed", False):
        return "unsafe_proposal_gate"
    if actual.get("language") not in (None, expected.get("language")):
        return "wrong_language"
    return None


def score(cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    failures: Counter[str] = Counter()
    by_feature: dict[str, dict[str, int]] = {}
    by_category: dict[str, dict[str, int]] = {}
    metric_hits: Counter[str] = Counter()
    gate_counts: Counter[str] = Counter()
    rows = []
    for case in cases:
        actual = predictions.get(case["id"], {})
        expected = {**case["expected"], "language": case.get("language")}
        reason = classify_failure(expected, actual)
        bucket = by_feature.setdefault(case["feature"], {"total": 0, "passed": 0})
        category_name = case.get("category", "unspecified")
        category = by_category.setdefault(category_name, {"total": 0, "passed": 0})
        bucket["total"] += 1
        category["total"] += 1
        bucket["passed"] += reason is None
        category["passed"] += reason is None
        if reason:
            failures[reason] += 1
        checks = {
            "command_and_task": actual.get("command_type") == expected["command_type"]
            and actual.get("task_type") == expected["task_type"],
            "parameters_exact": actual.get("parameters", {}) == expected.get("parameters", {}),
            "missing_fields_exact": sorted(actual.get("missing_fields", []))
            == sorted(expected.get("missing_fields", [])),
            "target_binding": actual.get("target_id") == expected.get("target_id"),
            "proposal_safety_gate": (
                actual.get("command_type") in {"create", "update", "cancel"}
                and not actual.get("missing_fields", [])
            )
            == expected.get("proposal_allowed", False),
            "language": actual.get("language") == case.get("language"),
        }
        for name, passed in checks.items():
            metric_hits[name] += passed
        gold_gate = case["expected"].get("proposal_allowed", False)
        predicted_gate = actual.get("command_type") in {
            "create",
            "update",
            "cancel",
        } and not actual.get("missing_fields", [])
        if gold_gate and predicted_gate:
            gate_counts["true_positive"] += 1
        elif gold_gate:
            gate_counts["false_negative"] += 1
        elif predicted_gate:
            gate_counts["false_positive"] += 1
        else:
            gate_counts["true_negative"] += 1
        rows.append(
            {
                "id": case["id"],
                "feature": case["feature"],
                "category": category_name,
                "passed": reason is None,
                "failure_type": reason,
            }
        )
    return {
        "case_count": len(cases),
        "prediction_count": len(predictions),
        "passed": sum(row["passed"] for row in rows),
        "accuracy": (sum(row["passed"] for row in rows) / len(cases)) if cases else 0.0,
        "by_feature": by_feature,
        "by_category": by_category,
        "metrics": {
            name: {"correct": count, "total": len(cases), "accuracy": count / len(cases)}
            for name, count in sorted(metric_hits.items())
        },
        "proposal_gate_confusion": {
            "true_positive": gate_counts["true_positive"],
            "false_positive": gate_counts["false_positive"],
            "true_negative": gate_counts["true_negative"],
            "false_negative": gate_counts["false_negative"],
            "false_accept_rate": gate_counts["false_positive"]
            / (gate_counts["false_positive"] + gate_counts["true_negative"])
            if gate_counts["false_positive"] + gate_counts["true_negative"]
            else 0.0,
            "precision": gate_counts["true_positive"]
            / (gate_counts["true_positive"] + gate_counts["false_positive"])
            if gate_counts["true_positive"] + gate_counts["false_positive"]
            else 0.0,
            "recall": gate_counts["true_positive"]
            / (gate_counts["true_positive"] + gate_counts["false_negative"])
            if gate_counts["true_positive"] + gate_counts["false_negative"]
            else 0.0,
        },
        "failure_taxonomy": dict(sorted(failures.items())),
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--predictions",
        type=Path,
        required=True,
        help="JSON object mapping case IDs to parser outputs",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = score(cases, predictions)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
