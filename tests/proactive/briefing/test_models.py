"""Tests for scheduled briefing data models."""

from datetime import date, datetime, time, timezone

import pytest

from app.agent.proactive.briefing.models import BriefingRun, BriefingSchedule


def make_schedule(**overrides):
    """Create a valid schedule with optional field overrides."""
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    values = {
        "id": "schedule-001",
        "workspace_id": "demo-workspace",
        "interval_days": 3,
        "local_time": time(8, 0),
        "timezone": "Asia/Singapore",
        "start_date": date(2026, 11, 3),
        "enabled": True,
        "next_run_at": datetime(2026, 11, 6, tzinfo=timezone.utc),
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return BriefingSchedule(**values)


def test_valid_schedule():
    schedule = make_schedule()
    assert schedule.interval_days == 3
    assert schedule.enabled is True


def test_invalid_interval():
    with pytest.raises(ValueError, match="positive integer"):
        make_schedule(interval_days=-3)


def test_invalid_timezone():
    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        make_schedule(timezone="Invalid/Timezone")


def test_naive_datetime_rejected():
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        make_schedule(created_at=datetime(2026, 11, 3))


def test_valid_run():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    run = BriefingRun(
        id="run-001",
        workspace_id="demo-workspace",
        schedule_id="schedule-001",
        scheduled_for=now,
        executed_at=now,
        status="success",
        result={"text": "Morning briefing completed."},
    )
    assert run.status == "success"


def test_invalid_run_status():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="Invalid run status"):
        BriefingRun(
            id="run-002",
            workspace_id="demo-workspace",
            schedule_id="schedule-001",
            scheduled_for=now,
            executed_at=now,
            status="unknown",
        )
