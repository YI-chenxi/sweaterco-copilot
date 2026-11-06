"""Deterministic service fakes for P2 contract acceptance tests.

These are executable test doubles, not P3/P4/P5 production implementations.
They expose the documented APIs and inject repository/executor failures so the
tests can verify caller-visible contract behavior without network or side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class MockFailure(RuntimeError):
    """Injected persistence or execution failure."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _next_occurrence(parameters: dict[str, Any], after: datetime) -> datetime:
    """Return first anchored local occurrence strictly after `after`."""
    interval = parameters["interval_days"]
    local_time = time.fromisoformat(parameters["local_time"])
    zone = ZoneInfo(parameters.get("timezone", "Asia/Singapore"))
    anchor = date.fromisoformat(parameters["start_date"])
    local_after = after.astimezone(zone)
    if local_after.date() <= anchor:
        candidate_date = anchor
    else:
        elapsed = (local_after.date() - anchor).days
        candidate_date = anchor + timedelta(days=((elapsed + interval - 1) // interval) * interval)
    candidate = datetime.combine(candidate_date, local_time, zone)
    if candidate.astimezone(UTC) <= after:
        candidate = datetime.combine(candidate_date + timedelta(days=interval), local_time, zone)
    return candidate.astimezone(UTC)


@dataclass
class MockBriefingService:
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    fail_next_save: bool = False
    fail_next_execute: bool = False

    def create_schedule(self, workspace_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not workspace_id:
            return {"ok": False, "error_code": "UNAUTHORIZED"}
        try:
            interval = parameters["interval_days"]
            if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
                raise ValueError
            time.fromisoformat(parameters["local_time"])
            date.fromisoformat(parameters["start_date"])
            ZoneInfo(parameters.get("timezone", "Asia/Singapore"))
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            return {"ok": False, "error_code": "INVALID_ARGUMENT"}
        if self.fail_next_save:
            self.fail_next_save = False
            return {"ok": False, "error_code": "STORAGE_ERROR"}
        schedule_id = str(uuid4())
        self.schedules[schedule_id] = {
            **parameters,
            "timezone": parameters.get("timezone", "Asia/Singapore"),
            "workspace_id": workspace_id,
            "id": schedule_id,
            "enabled": True,
        }
        return {"ok": True, "status": "created", "id": schedule_id}

    def update_schedule(
        self, workspace_id: str, schedule_id: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        row = self.schedules.get(schedule_id)
        if row is None or row["workspace_id"] != workspace_id:
            return {"ok": False, "error_code": "NOT_FOUND"}
        row.update(parameters)
        return {"ok": True, "status": "updated", "id": schedule_id}

    def cancel_schedule(self, workspace_id: str, schedule_id: str) -> dict[str, Any]:
        row = self.schedules.get(schedule_id)
        if row is None or row["workspace_id"] != workspace_id:
            return {"ok": False, "error_code": "NOT_FOUND"}
        row["enabled"] = False
        return {"ok": True, "status": "cancelled", "id": schedule_id}

    def next_run(self, schedule_id: str, after: datetime) -> datetime:
        return _next_occurrence(self.schedules[schedule_id], _aware_utc(after))

    def run_due_briefings(self, now: datetime) -> list[dict[str, Any]]:
        now_utc = _aware_utc(now)
        due = []
        for row in self.schedules.values():
            if not row.get("enabled", True):
                continue
            due_at = row.get("next_run_at") or self.next_run(
                row["id"], now_utc - timedelta(microseconds=1)
            )
            due_at = _aware_utc(due_at)
            key = (row["id"], due_at.isoformat())
            if due_at > now_utc:
                continue
            if key in self.runs and self.runs[key]["status"] == "succeeded":
                continue
            if self.fail_next_execute:
                self.fail_next_execute = False
                self.runs[key] = {"status": "failed", "attempts": 1}
                due.append({"id": row["id"], "ok": False, "error_code": "EXECUTION_ERROR"})
                continue
            prior = self.runs.get(key)
            if prior and prior["status"] == "failed":
                prior["attempts"] += 1
                prior["status"] = "succeeded"
            else:
                self.runs[key] = {"status": "succeeded", "attempts": 1}
            row["next_run_at"] = due_at + timedelta(days=row["interval_days"])
            due.append({"id": row["id"], "ok": True, "scheduled_for": due_at.isoformat()})
        return due


@dataclass
class MockWatchService:
    watches: dict[str, dict[str, Any]] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    fail_next_check: bool = False

    def create_watch(self, workspace_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not workspace_id:
            return {"ok": False, "error_code": "UNAUTHORIZED"}
        if parameters.get("condition_type") not in {"no_progress_by_deadline", "status_equals"}:
            return {"ok": False, "error_code": "UNSUPPORTED_CONDITION"}
        try:
            if not parameters.get("order_id"):
                raise ValueError
            interval = parameters["check_interval_minutes"]
            if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
                raise ValueError
            if parameters["condition_type"] == "no_progress_by_deadline":
                deadline = datetime.fromisoformat(parameters["deadline_at"].replace("Z", "+00:00"))
                _aware_utc(deadline)
            elif not parameters.get("expected_status"):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error_code": "INVALID_ARGUMENT"}
        watch_id = str(uuid4())
        self.watches[watch_id] = {
            **parameters,
            "workspace_id": workspace_id,
            "id": watch_id,
            "enabled": True,
        }
        return {"ok": True, "status": "created", "id": watch_id}

    def cancel_watch(self, workspace_id: str, watch_id: str) -> dict[str, Any]:
        row = self.watches.get(watch_id)
        if row is None or row["workspace_id"] != workspace_id:
            return {"ok": False, "error_code": "NOT_FOUND"}
        row["enabled"] = False
        return {"ok": True, "status": "cancelled", "id": watch_id}

    def run_due_watches(self, now: datetime, observed_status: str) -> list[dict[str, Any]]:
        now_utc = _aware_utc(now)
        outcomes = []
        for row in self.watches.values():
            if not row["enabled"]:
                continue
            key = (row["id"], now_utc.isoformat())
            if key in self.checks:
                continue
            if self.fail_next_check:
                self.fail_next_check = False
                outcomes.append({"ok": False, "error_code": "EXECUTION_ERROR", "id": row["id"]})
                continue
            if row["condition_type"] == "status_equals":
                triggered = observed_status == row["expected_status"]
            else:
                deadline = _aware_utc(
                    datetime.fromisoformat(row["deadline_at"].replace("Z", "+00:00"))
                )
                triggered = now_utc >= deadline and observed_status == row.get("baseline_status")
            evidence = {
                "watch_id": row["id"],
                "checked_at": now_utc.isoformat(),
                "observed_status": observed_status,
                "triggered": triggered,
            }
            self.checks[key] = evidence
            if triggered:
                self.alerts.append(evidence)
            outcomes.append({"ok": True, **evidence})
        return outcomes


@dataclass
class MockActionService:
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    effects: list[tuple[str, str]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    fail_next_execute: bool = False

    def propose_action(
        self, workspace_id: str, command: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        if not workspace_id:
            return {"ok": False, "error_code": "UNAUTHORIZED"}
        proposal_id = str(uuid4())
        self.proposals[proposal_id] = {
            "workspace_id": workspace_id,
            "command": command,
            "state": "pending_confirmation",
            "expires_at": _aware_utc(now) + timedelta(minutes=15),
            "idempotency_key": str(uuid4()),
        }
        return {"ok": True, "status": "pending_confirmation", "id": proposal_id}

    def confirm_action(self, workspace_id: str, proposal_id: str, now: datetime) -> dict[str, Any]:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal["workspace_id"] != workspace_id:
            return {"ok": False, "error_code": "NOT_FOUND"}
        if _aware_utc(now) >= proposal["expires_at"]:
            proposal["state"] = "expired"
            return {"ok": False, "error_code": "EXPIRED"}
        if proposal["state"] == "executed":
            return {"ok": True, "status": "already_executed", "id": proposal_id}
        if proposal["state"] not in {"pending_confirmation", "failed"}:
            return {"ok": False, "error_code": "CONFLICT"}
        if self.fail_next_execute:
            self.fail_next_execute = False
            proposal["state"] = "failed"
            self.audit.append({"proposal_id": proposal_id, "event": "execution_failed"})
            return {"ok": False, "error_code": "EXECUTION_ERROR"}
        proposal["state"] = "executed"
        effect = (proposal_id, proposal["idempotency_key"])
        if effect not in self.effects:
            self.effects.append(effect)
        self.audit.append({"proposal_id": proposal_id, "event": "executed"})
        return {"ok": True, "status": "executed", "id": proposal_id}

    def reject_action(self, workspace_id: str, proposal_id: str) -> dict[str, Any]:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal["workspace_id"] != workspace_id:
            return {"ok": False, "error_code": "NOT_FOUND"}
        if proposal["state"] != "pending_confirmation":
            return {"ok": False, "error_code": "CONFLICT"}
        proposal["state"] = "rejected"
        self.audit.append({"proposal_id": proposal_id, "event": "rejected"})
        return {"ok": True, "status": "rejected", "id": proposal_id}
