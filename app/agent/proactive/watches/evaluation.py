"""P5-local due state and pure batch preparation. Nothing here reads orders."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

from app.agent.contracts import ToolResult
from app.agent.proactive.watches.models import (
    Alert,
    CheckOutcome,
    CheckRecord,
    Watch,
    WatchValidationError,
    require_utc,
)
from app.agent.proactive.watches.rules import baseline_is_valid, evaluate_condition
from app.agent.proactive.watches.serialization import record_data, uuid_string


@dataclass(frozen=True)
class WatchRuntime:
    anchor_at: datetime
    next_check_at: datetime
    baseline: ToolResult | None = None
    baseline_at: datetime | None = None
    alert_latched: bool = False

    def __post_init__(self):
        require_utc(self.anchor_at, "anchor_at")
        require_utc(self.next_check_at, "next_check_at")
        if (self.baseline is None) != (self.baseline_at is None):
            raise WatchValidationError("Baseline and capture time must be stored together")
        if self.baseline_at is not None:
            require_utc(self.baseline_at, "baseline_at")
        if type(self.alert_latched) is not bool:
            raise WatchValidationError("alert_latched must be boolean")


@dataclass(frozen=True)
class EvaluationBatch:
    record: CheckRecord
    alert: Alert | None
    runtime: WatchRuntime


def utc_text(value: datetime) -> str:
    require_utc(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def next_check(watch: Watch, runtime: WatchRuntime, now: datetime) -> datetime:
    """Fixed UTC grid; never backfill missed observations. Deadline is an exact boundary."""
    require_utc(now, "now")
    step = timedelta(minutes=watch.check_interval_minutes)
    if watch.deadline_at is not None:
        if now < watch.deadline_at and runtime.baseline is not None:
            return watch.deadline_at
        anchor = watch.deadline_at if now >= watch.deadline_at else runtime.anchor_at
    else:
        anchor = runtime.anchor_at
    following = anchor + ((now - anchor) // step + 1) * step
    if watch.deadline_at is not None and now < watch.deadline_at:
        following = min(following, watch.deadline_at)
    return following


def prepare_evaluation(
    watch: Watch,
    runtime: WatchRuntime,
    observation: ToolResult | None,
    *,
    now: datetime,
    record_id: str,
    alert_id: str,
) -> EvaluationBatch:
    require_utc(now, "now")
    record_id, alert_id = uuid_string(record_id), uuid_string(alert_id)
    baseline, captured = runtime.baseline, runtime.baseline_at
    before_deadline = watch.deadline_at is not None and now < watch.deadline_at
    valid_current = False
    if before_deadline:
        valid_current = baseline_is_valid(watch, observation, now=now)
        if baseline is None and valid_current:
            # Store only the fields consumed by the deterministic rules, plus source evidence.
            fields = ("order_id", "status", "last_activity_date", "current_stage")
            baseline = ToolResult(
                {k: deepcopy(observation.data[k]) for k in fields if k in observation.data},
                list(deepcopy(observation.evidence)),
            )
            captured = now  # actual supplied check time, never the missed scheduled time
    baseline_ok = (
        baseline is not None
        and captured is not None
        and watch.deadline_at is not None
        and runtime.anchor_at <= captured < watch.deadline_at
        and captured <= now
        and baseline_is_valid(watch, baseline, now=captured)
    )
    if (before_deadline and not valid_current) or (
        watch.condition_type == "no_progress_by_deadline"
        and not before_deadline
        and not baseline_ok
    ):
        record = CheckRecord(
            record_id,
            watch.workspace_id,
            watch.id,
            now,
            {},
            (),
            CheckOutcome.UNAVAILABLE,
        )
    else:
        try:
            record = evaluate_condition(
                watch,
                observation,
                now=now,
                record_id=record_id,
                baseline=baseline if baseline_ok else None,
            )
        except (WatchValidationError, TypeError, ValueError, AttributeError):
            # Malformed source values are unknown, never a business conclusion.
            record = CheckRecord(
                record_id,
                watch.workspace_id,
                watch.id,
                now,
                {},
                (),
                CheckOutcome.UNAVAILABLE,
            )
    latched = runtime.alert_latched
    alert = None
    if record.outcome == CheckOutcome.MATCHED and not latched:
        alert = Alert(**{**asdict(record), "id": alert_id, "evidence": record.evidence})
        latched = True
    elif record.outcome == CheckOutcome.NOT_MATCHED and watch.condition_type == "status_equals":
        latched = False
    updated = replace(
        runtime,
        baseline=baseline,
        baseline_at=captured,
        alert_latched=latched,
    )
    updated = replace(updated, next_check_at=next_check(watch, updated, now))
    return EvaluationBatch(record, alert, updated)


def check_result(record: CheckRecord) -> dict:
    return {
        "ok": True,
        "status": record.outcome.value,
        "id": record.id,
        "data": record_data(record),
        "error_code": None,
        "message": (
            "Check recorded; source evidence unavailable, no alert generated."
            if record.outcome == CheckOutcome.UNAVAILABLE
            else "Watch check recorded."
        ),
    }


def validate_batch(watch: Watch, previous: WatchRuntime, batch: EvaluationBatch, now: datetime):
    """Validate the internal callback boundary before storage is mutated."""
    if not isinstance(batch, EvaluationBatch):
        raise WatchValidationError("Invalid evaluation batch")
    records = [batch.record] + ([batch.alert] if batch.alert else [])
    for record in records:
        if (record.workspace_id, record.watch_id) != (watch.workspace_id, watch.id) or (
            record.timestamp < now or record.timestamp != batch.record.timestamp
        ):
            raise WatchValidationError("Evaluation batch association mismatch")
    if batch.runtime.anchor_at != previous.anchor_at or (
        batch.runtime.next_check_at <= batch.record.timestamp
    ):
        raise WatchValidationError("Invalid scheduling transition")
    if previous.baseline is not None and (
        previous.baseline != batch.runtime.baseline
        or previous.baseline_at != batch.runtime.baseline_at
    ):
        raise WatchValidationError("Baseline is immutable")


def runtime_data(runtime: WatchRuntime) -> dict:
    return {
        "anchor_at": utc_text(runtime.anchor_at),
        "next_check_at": utc_text(runtime.next_check_at),
        "baseline": asdict(runtime.baseline) if runtime.baseline is not None else None,
        "baseline_at": utc_text(runtime.baseline_at) if runtime.baseline_at else None,
        "alert_latched": runtime.alert_latched,
    }


def load_runtime(data: dict) -> WatchRuntime:
    from app.agent.contracts import Evidence

    baseline = data["baseline"]
    return WatchRuntime(
        datetime.fromisoformat(data["anchor_at"]),
        datetime.fromisoformat(data["next_check_at"]),
        ToolResult(
            baseline["data"],
            [Evidence(**item) for item in baseline["evidence"]],
            baseline["warnings"],
        )
        if baseline is not None
        else None,
        datetime.fromisoformat(data["baseline_at"]) if data["baseline_at"] is not None else None,
        data["alert_latched"],
    )
