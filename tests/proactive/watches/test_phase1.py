"""Isolated P5 tests: injected facts/time/IDs, no environment or network."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.contracts import Evidence, ToolResult
from app.agent.proactive.watches.models import (
    Alert,
    CheckOutcome,
    CheckRecord,
    Watch,
    WatchValidationError,
)
from app.agent.proactive.watches.rules import evaluate_condition, validate_parameters

WATCH_ID = "89a21d91-2953-4975-a727-10e05ef75496"
RECORD_ID = "34fb7513-0d07-4529-9412-1a7d3daa0580"
NOW = datetime(2026, 4, 1, 8, tzinfo=timezone.utc)


def params(**changes):
    result = {
        "order_id": "ORD-002",
        "condition_type": "no_progress_by_deadline",
        "check_interval_minutes": 15,
        "deadline_at": NOW.isoformat(),
    }
    result.update(changes)
    return result


def watch(**changes):
    return Watch(id=WATCH_ID, workspace_id="demo", **validate_parameters(params(**changes)))


def row(**changes):
    data = {
        "order_id": "ORD-002",
        "status": "IN_PROGRESS",
        "last_activity_date": "2026-03-23",
        "current_stage": "WASHING",
    }
    data.update(changes)
    return ToolResult(data, [Evidence("orders.csv", "ORD-002", "Mock row snapshot")])


def evaluate(current=None, baseline=None, **changes):
    return evaluate_condition(
        watch(),
        current,
        baseline=baseline,
        now=changes.get("now", NOW),
        record_id=RECORD_ID,
    )


@pytest.mark.parametrize("deadline", [NOW.isoformat(), "2026-04-01T08:00:00Z"])
def test_valid_parameters_normalize_without_mutating(deadline):
    original = params(order_id=" ord-002 ", deadline_at=deadline)
    saved = deepcopy(original)
    normalized = validate_parameters(original)
    assert original == saved
    assert normalized["order_id"] == "ORD-002"
    assert normalized["deadline_at"] == NOW
    assert set(normalized) == set(original)


@pytest.mark.parametrize("field", ["order_id", "condition_type", "check_interval_minutes"])
def test_required_fields(field):
    data = params()
    del data[field]
    with pytest.raises(WatchValidationError) as exc:
        validate_parameters(data)
    assert exc.value.error_code == "MISSING_FIELD"


@pytest.mark.parametrize("interval", [0, -1, True, False, 1.5, "15", None])
def test_invalid_interval(interval):
    with pytest.raises(WatchValidationError):
        validate_parameters(params(check_interval_minutes=interval))


@pytest.mark.parametrize("condition", ["production_drop", "STATUS_EQUALS", [], 1, ""])
def test_unsupported_condition(condition):
    with pytest.raises(WatchValidationError) as exc:
        validate_parameters(params(condition_type=condition))
    assert exc.value.error_code == "UNSUPPORTED_CONDITION"


@pytest.mark.parametrize(
    "deadline",
    [
        "2026-04-01",
        "2026-04-01T08:00:00",
        "2026-04-01T08:00:00+08:00",
        "not-a-date",
        "2026-02-30T08:00:00Z",
        NOW,
        5,
        None,
    ],
)
def test_invalid_deadline(deadline):
    with pytest.raises(WatchValidationError):
        validate_parameters(params(deadline_at=deadline))


@pytest.mark.parametrize("data", [None, [], "watch", 5])
def test_parameter_container(data):
    with pytest.raises(WatchValidationError):
        validate_parameters(data)


@pytest.mark.parametrize("order_id", [None, "", "   ", 42, []])
def test_invalid_order_identifier(order_id):
    with pytest.raises(WatchValidationError):
        validate_parameters(params(order_id=order_id))


@pytest.mark.parametrize("extra", ["baseline", "paused", "next_run_at", "confirmed"])
def test_no_unagreed_public_parameters(extra):
    with pytest.raises(WatchValidationError):
        validate_parameters(params(**{extra: True}))


def test_reject_undefined_combinations_and_missing_condition_fields():
    with pytest.raises(WatchValidationError):
        validate_parameters(params(expected_status="COMPLETE"))
    with pytest.raises(WatchValidationError):
        validate_parameters(params(condition_type="status_equals", expected_status="COMPLETE"))
    for condition, required in [
        ("no_progress_by_deadline", "deadline_at"),
        ("status_equals", "expected_status"),
    ]:
        data = params(condition_type=condition)
        data.pop("deadline_at")
        with pytest.raises(WatchValidationError) as exc:
            validate_parameters(data)
        assert exc.value.error_code == "MISSING_FIELD"
        assert required in str(exc.value)


@pytest.mark.parametrize("expected_status", ["PACKING", "complete", "UNKNOWN", [], 1, ""])
def test_status_values_are_order_status_not_stage(expected_status):
    with pytest.raises(WatchValidationError):
        watch(condition_type="status_equals", deadline_at=None, expected_status=expected_status)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad-id"),
        ("id", None),
        ("workspace_id", " "),
        ("workspace_id", 1),
        ("order_id", " ord-002 "),
        ("deadline_at", NOW.replace(tzinfo=None)),
        ("deadline_at", NOW.astimezone(timezone(timedelta(hours=8)))),
    ],
)
def test_direct_model_validation(field, value):
    with pytest.raises(WatchValidationError):
        replace(watch(), **{field: value})


def test_watch_is_frozen():
    with pytest.raises(FrozenInstanceError):
        watch().condition_type = "unsupported"


@pytest.mark.parametrize(
    "delta,outcome",
    [
        (-1, CheckOutcome.NOT_DUE),
        (0, CheckOutcome.MATCHED),
        (1, CheckOutcome.MATCHED),
    ],
)
def test_deadline_boundary(delta, outcome):
    result = evaluate(row(), row(), now=NOW + timedelta(microseconds=delta))
    assert result.outcome == outcome
    assert result.workspace_id == "demo"
    assert result.watch_id == WATCH_ID
    assert result.timestamp == NOW + timedelta(microseconds=delta)


def test_not_due_is_not_a_normal_business_conclusion():
    assert evaluate(None, None, now=NOW - timedelta(seconds=1)).outcome == CheckOutcome.NOT_DUE


@pytest.mark.parametrize(
    "now",
    [
        None,
        "2026-04-01",
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=8))),
    ],
)
def test_invalid_evaluation_clock(now):
    with pytest.raises(WatchValidationError):
        evaluate(row(), row(), now=now)


@pytest.mark.parametrize(
    "current",
    [
        None,
        ToolResult({}),
        row(order_id="ORD-003"),
        row(status="PACKING"),
        row(status=None),
        row(last_activity_date="bad"),
        row(last_activity_date=None),
        row(last_activity_date="2026-02-30"),
        row(last_activity_date="2026-04-02"),
        ToolResult(row().data),
        ToolResult(row().data, row().evidence, ["Source unavailable"]),
        ToolResult(row().data, [Evidence("", "ORD-002", "Invalid source")]),
    ],
)
def test_unavailable_current_data_never_matches(current):
    assert evaluate(current, row()).outcome == CheckOutcome.UNAVAILABLE


@pytest.mark.parametrize(
    "baseline",
    [
        None,
        ToolResult({}),
        row(order_id="ORD-003"),
        row(status="COMPLETE"),
        row(last_activity_date="bad"),
        row(last_activity_date="2026-03-24"),
        ToolResult(row().data),
    ],
)
def test_missing_or_inconsistent_baseline_is_not_guessed(baseline):
    assert evaluate(row(), baseline).outcome == CheckOutcome.UNAVAILABLE


def test_activity_change_completion_and_stage_inconsistency():
    assert evaluate(row(last_activity_date="2026-03-24"), row()).outcome == CheckOutcome.NOT_MATCHED
    assert evaluate(row(status="COMPLETE"), row()).outcome == CheckOutcome.NOT_MATCHED
    assert evaluate(row(current_stage="PACKING"), row()).outcome == CheckOutcome.UNAVAILABLE


@pytest.mark.parametrize(
    "expected,current,outcome",
    [
        ("COMPLETE", "COMPLETE", CheckOutcome.MATCHED),
        ("COMPLETE", "IN_PROGRESS", CheckOutcome.NOT_MATCHED),
        ("IN_PROGRESS", "IN_PROGRESS", CheckOutcome.MATCHED),
        ("IN_PROGRESS", "COMPLETE", CheckOutcome.NOT_MATCHED),
    ],
)
def test_status_rule_uses_exact_current_status_without_baseline(expected, current, outcome):
    result = evaluate_condition(
        watch(condition_type="status_equals", expected_status=expected, deadline_at=None),
        row(status=current),
        now=NOW,
        record_id=RECORD_ID,
    )
    assert result.outcome == outcome
    assert result.evidence == tuple(row().evidence)


def test_rule_is_reproducible_and_detaches_observations():
    current, baseline = row(), row()
    before = deepcopy((current, baseline))
    first, second = evaluate(current, baseline), evaluate(current, baseline)
    assert first == second
    assert (current, baseline) == before
    current.data["last_activity_date"] = "2026-04-01"
    current.evidence.clear()
    assert first.observed_value["current"]["last_activity_date"] == "2026-03-23"
    assert len(first.evidence) == 2


def record_values():
    return {
        "id": RECORD_ID,
        "workspace_id": "demo",
        "watch_id": WATCH_ID,
        "timestamp": NOW,
        "observed_value": {"status": "COMPLETE"},
        "evidence": tuple(row().evidence),
        "outcome": CheckOutcome.MATCHED,
    }


def test_alert_accepts_only_matched_evidence_backed_records():
    assert Alert(**record_values()).outcome == CheckOutcome.MATCHED
    for outcome in [CheckOutcome.NOT_DUE, CheckOutcome.NOT_MATCHED, CheckOutcome.UNAVAILABLE]:
        with pytest.raises(WatchValidationError):
            Alert(**{**record_values(), "outcome": outcome})


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad"),
        ("watch_id", "bad"),
        ("workspace_id", ""),
        ("timestamp", NOW.replace(tzinfo=None)),
        ("outcome", "matched"),
        ("observed_value", []),
        ("observed_value", {"x": float("nan")}),
        ("observed_value", {"x": float("inf")}),
        ("observed_value", {"x": NOW}),
        ("observed_value", {1: "non-string key"}),
        ("evidence", ()),
        ("evidence", ("invented evidence",)),
    ],
)
def test_record_validation(field, value):
    with pytest.raises(WatchValidationError):
        CheckRecord(**{**record_values(), field: value})


def test_record_detaches_input_and_keeps_workspace():
    values = record_values()
    result = CheckRecord(**values)
    values["observed_value"]["status"] = "IN_PROGRESS"
    assert result.observed_value["status"] == "COMPLETE"
    assert replace(result, workspace_id="other").workspace_id == "other"


def test_missing_activity_date_is_explicitly_unavailable():
    current = row()
    del current.data["last_activity_date"]
    assert evaluate(current, row()).outcome == CheckOutcome.UNAVAILABLE
