"""Check consistent configuration versions across briefing repositories."""

from dataclasses import replace
from datetime import date, datetime, time, timezone

import pytest

from app.agent.proactive.briefing.memory_repository import InMemoryBriefingRepository
from app.agent.proactive.briefing.models import BriefingSchedule
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository


@pytest.mark.parametrize("repository_type", ["memory", "sqlite"])
def test_update_increments_version_and_rejects_stale_object(
    tmp_path, repository_type
):
    if repository_type == "memory":
        repo = InMemoryBriefingRepository()
    else:
        repo = SQLiteBriefingRepository(tmp_path / "briefing.db")

    now = datetime(2026, 11, 8, tzinfo=timezone.utc)

    schedule = BriefingSchedule(
        id="version-consistency-test",
        workspace_id="group02",
        interval_days=1,
        local_time=time(9, 0),
        timezone="Asia/Singapore",
        start_date=date(2026, 11, 9),
        enabled=True,
        next_run_at=None,
        created_at=now,
        updated_at=now,
    )

    repo.create_schedule(schedule)

    original = repo.get_schedule("group02", schedule.id)
    assert original.config_version == 1

    repo.update_schedule(replace(original, interval_days=3))

    updated = repo.get_schedule("group02", schedule.id)
    assert updated.interval_days == 3
    assert updated.config_version == 2

    with pytest.raises(ValueError, match="version conflict"):
        repo.update_schedule(replace(original, interval_days=7))

    final = repo.get_schedule("group02", schedule.id)
    assert final.interval_days == 3
    assert final.config_version == 2
