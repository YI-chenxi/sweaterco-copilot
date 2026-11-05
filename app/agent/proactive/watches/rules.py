"""Pure rules over injected ToolResult snapshots: no IO, clock or LLM calls."""

from copy import deepcopy
from datetime import date, datetime
from typing import Any

from app.agent.contracts import Evidence, ToolResult
from app.agent.proactive.watches.models import (
    CONDITIONS,
    CheckOutcome,
    CheckRecord,
    Watch,
    WatchValidationError,
    require_utc,
)
from app.tools.order_status.config import ALLOWED_STATUSES

_FIELDS = frozenset(
    {
        "order_id",
        "condition_type",
        "deadline_at",
        "expected_status",
        "check_interval_minutes",
    }
)


def validate_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Validate existing contract parameters; never add defaults or public fields."""
    if not isinstance(parameters, dict):
        raise WatchValidationError("parameters must be a dictionary")
    if set(parameters) - _FIELDS:
        raise WatchValidationError("Unknown watch parameter")
    for field in ("order_id", "condition_type", "check_interval_minutes"):
        if field not in parameters or parameters[field] is None:
            raise WatchValidationError(f"{field} is required", "MISSING_FIELD")
    if not isinstance(parameters["order_id"], str) or not parameters["order_id"].strip():
        raise WatchValidationError("order_id must be a nonempty string")
    condition = parameters["condition_type"]
    if not isinstance(condition, str) or condition not in CONDITIONS:
        raise WatchValidationError("Unsupported condition", "UNSUPPORTED_CONDITION")
    result = deepcopy(parameters)
    result["order_id"] = result["order_id"].strip().upper()
    if result.get("deadline_at") is not None:
        raw = result["deadline_at"]
        if not isinstance(raw, str):
            raise WatchValidationError("deadline_at must be a UTC ISO 8601 string")
        try:
            result["deadline_at"] = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WatchValidationError("Invalid ISO deadline_at") from exc
    # Reuse exactly the same validation as direct local model construction.
    Watch(
        id="00000000-0000-4000-8000-000000000001",
        workspace_id="validation-only",
        **result,
    )
    return result


def _row(result: ToolResult | None, order_id: str) -> dict[str, Any] | None:
    if (
        not isinstance(result, ToolResult)
        or not isinstance(result.data, dict)
        or result.data.get("order_id") != order_id
        or not isinstance(result.data.get("status"), str)
        or result.data["status"] not in ALLOWED_STATUSES
        or result.warnings
        or not isinstance(result.evidence, (list, tuple))
        or not result.evidence
        or not all(
            isinstance(e, Evidence)
            and all(isinstance(v, str) and v.strip() for v in (e.source, e.reference, e.detail))
            for e in result.evidence
        )
    ):
        return None
    return result.data


def _activity(row: dict[str, Any], now: datetime) -> date | None:
    value = row.get("last_activity_date")
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        activity = date.fromisoformat(value)
    except ValueError:
        return None
    return activity if activity <= now.date() else None


def evaluate_condition(
    watch: Watch,
    observation: ToolResult | None,
    *,
    now: datetime,
    record_id: str,
    baseline: ToolResult | None = None,
) -> CheckRecord:
    """Return an internal record; baseline is an injected snapshot, not a slot.

    Pending business decisions and conservative semantics are documented in README.
    The caller provides IDs and time to keep this function reproducible.
    """
    if not isinstance(watch, Watch):
        raise WatchValidationError("watch must be a Watch")
    require_utc(now, "now")
    values: dict[str, Any] = {}
    evidence: tuple[Evidence, ...] = ()

    def record(outcome: CheckOutcome) -> CheckRecord:
        return CheckRecord(
            id=record_id,
            workspace_id=watch.workspace_id,
            watch_id=watch.id,
            timestamp=now,
            observed_value=values,
            evidence=evidence,
            outcome=outcome,
        )

    if watch.deadline_at is not None and now < watch.deadline_at:
        return record(CheckOutcome.NOT_DUE)
    current = _row(observation, watch.order_id)
    if current is None:
        return record(CheckOutcome.UNAVAILABLE)
    values["current"] = {
        key: current[key] for key in ("order_id", "status", "last_activity_date") if key in current
    }
    evidence = tuple(observation.evidence)
    if watch.condition_type == "status_equals":
        return record(
            CheckOutcome.MATCHED
            if current["status"] == watch.expected_status
            else CheckOutcome.NOT_MATCHED
        )
    current_date = _activity(current, now)
    if current_date is None:
        return record(CheckOutcome.UNAVAILABLE)
    # Completed orders should not be labelled stalled just because their activity is old.
    if current["status"] == "COMPLETE":
        return record(CheckOutcome.NOT_MATCHED)
    previous = _row(baseline, watch.order_id)
    if previous is None:
        return record(CheckOutcome.UNAVAILABLE)
    previous_date = _activity(previous, now)
    values["baseline"] = {
        key: previous[key]
        for key in ("order_id", "status", "last_activity_date")
        if key in previous
    }
    evidence += tuple(baseline.evidence)
    if previous_date is None or current_date < previous_date or previous["status"] == "COMPLETE":
        return record(CheckOutcome.UNAVAILABLE)
    if current_date > previous_date:
        return record(CheckOutcome.NOT_MATCHED)
    # A changed stage with no activity-date update is inconsistent/insufficient data.
    old_stage, new_stage = previous.get("current_stage"), current.get("current_stage")
    if old_stage is not None and new_stage is not None and old_stage != new_stage:
        return record(CheckOutcome.UNAVAILABLE)
    return record(CheckOutcome.MATCHED)


def baseline_is_valid(watch: Watch, observation: ToolResult | None, *, now: datetime) -> bool:
    """P5 initialization uses the same fact/evidence/date checks as evaluation."""
    require_utc(now, "now")
    row = _row(observation, watch.order_id)
    return (
        row is not None
        and _activity(row, now) is not None
        and ("current_stage" not in row or isinstance(row["current_stage"], str))
    )
