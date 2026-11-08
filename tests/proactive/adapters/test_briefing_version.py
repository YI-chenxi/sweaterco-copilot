"""Tests for briefing configuration versioning."""

from datetime import datetime, timezone

from app.agent.proactive.briefing.models import BriefingSchedule
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository


def make_schedule():
    return BriefingSchedule(
        id="schedule-version-test",
        workspace_id="group02",
        interval_days=1,
        local_time=__import__("datetime").time(9, 0),
        timezone="Asia/Singapore",
        start_date=__import__("datetime").date(2026, 11, 9),
        enabled=True,
        next_run_at=None,
        created_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
    )


def test_new_schedule_starts_at_version_one(tmp_path):
    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    repo.create_schedule(make_schedule())

    saved = repo.get_schedule("group02", "schedule-version-test")

    assert saved is not None
    assert saved.config_version == 1


def test_config_version_persists_after_reopen(tmp_path):
    database = tmp_path / "briefing.db"

    repo = SQLiteBriefingRepository(database)
    repo.create_schedule(make_schedule())

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("group02", "schedule-version-test")

    assert saved is not None
    assert saved.config_version == 1


def test_update_increments_config_version(tmp_path):
    from dataclasses import replace

    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    repo.create_schedule(make_schedule())

    existing = repo.get_schedule("group02", "schedule-version-test")
    updated = replace(existing, interval_days=3)

    repo.update_schedule(updated)

    saved = repo.get_schedule("group02", "schedule-version-test")
    assert saved.interval_days == 3
    assert saved.config_version == 2


def test_stale_version_cannot_overwrite_newer_update(tmp_path):
    from dataclasses import replace

    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    repo.create_schedule(make_schedule())

    original = repo.get_schedule("group02", "schedule-version-test")

    first_update = replace(original, interval_days=3)
    repo.update_schedule(first_update)

    stale_update = replace(original, interval_days=7)

    import pytest

    with pytest.raises(ValueError, match="version|conflict|stale"):
        repo.update_schedule(stale_update)

    saved = repo.get_schedule("group02", "schedule-version-test")
    assert saved.interval_days == 3
    assert saved.config_version == 2


def test_config_version_survives_repository_restart(tmp_path):
    from dataclasses import replace

    database = tmp_path / "briefing.db"
    repo = SQLiteBriefingRepository(database)
    repo.create_schedule(make_schedule())

    existing = repo.get_schedule("group02", "schedule-version-test")
    repo.update_schedule(replace(existing, interval_days=3))

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("group02", "schedule-version-test")

    assert saved.interval_days == 3
    assert saved.config_version == 2
