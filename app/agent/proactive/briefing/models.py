"""Data models for scheduled morning briefings."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _require_utc(value: datetime, name: str) -> None:
    """Require a timezone-aware UTC datetime."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass
class BriefingSchedule:
    """A persistent morning briefing schedule."""

    id: str
    workspace_id: str
    interval_days: int
    local_time: time
    timezone: str
    start_date: date
    enabled: bool
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None
    config_version: int = 1

    def __post_init__(self) -> None:
        if not self.id or not self.workspace_id.strip():
            raise ValueError("id and workspace_id are required")

        if (isinstance(self.interval_days, bool)
                or not isinstance(self.interval_days, int)
                or self.interval_days <= 0):
            raise ValueError("interval_days must be a positive integer")

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise ValueError("Invalid IANA timezone") from exc

        if not isinstance(self.local_time, time):
            raise ValueError("local_time must be a time object")
        if self.local_time.tzinfo is not None:
            raise ValueError("local_time must not contain timezone information")

        if not isinstance(self.start_date, date) or isinstance(self.start_date, datetime):
            raise ValueError("start_date must be a date")

        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be boolean")

        if type(self.config_version) is not int or self.config_version < 1:
            raise ValueError("config_version must be a positive integer")

        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")

        if self.next_run_at is not None:
            _require_utc(self.next_run_at, "next_run_at")

        if self.cancelled_at is not None:
            _require_utc(self.cancelled_at, "cancelled_at")
            if self.enabled or self.next_run_at is not None:
                raise ValueError(
                    "Cancelled schedules must be disabled with no next run"
                )


@dataclass
class BriefingRun:
    """One scheduled execution of a morning briefing."""

    id: str
    workspace_id: str
    schedule_id: str
    scheduled_for: datetime
    executed_at: datetime
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.workspace_id.strip() or not self.schedule_id:
            raise ValueError("Run identifiers are required")

        _require_utc(self.scheduled_for, "scheduled_for")
        _require_utc(self.executed_at, "executed_at")

        if self.status not in {"success", "failed"}:
            raise ValueError("Invalid run status")
