"""Tests for the in-memory briefing repository."""

from dataclasses import replace
from datetime import date, datetime, time, timezone

import pytest

from app.agent.proactive.briefing.memory_repository import InMemoryBriefingRepository
from app.agent.proactive.briefing.models import BriefingRun, BriefingSchedule

NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)


def make_schedule(workspace_id="workspace-a", schedule_id="schedule-001"):
    return BriefingSchedule(
        id=schedule_id,
        workspace_id=workspace_id,
        interval_days=3,
        local_time=time(8, 0),
        timezone="Asia/Singapore",
        start_date=date(2026, 11, 3),
        enabled=True,
        next_run_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def test_create_and_get_schedule():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    schedule = repo.get_schedule("workspace-a", "schedule-001")

    assert schedule is not None
    assert schedule.interval_days == 3


def test_workspace_isolation():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    assert repo.get_schedule("workspace-b", "schedule-001") is None
    assert repo.list_schedules("workspace-b") == []


def test_update_schedule():
    repo = InMemoryBriefingRepository()
    schedule = make_schedule()
    repo.create_schedule(schedule)

    repo.update_schedule(replace(schedule, enabled=False))

    assert repo.get_schedule("workspace-a", schedule.id).enabled is False


def test_duplicate_schedule_rejected():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    with pytest.raises(ValueError, match="already exists"):
        repo.create_schedule(make_schedule())


def test_due_schedule_detection():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    assert len(repo.list_due_schedules(NOW)) == 1


def test_future_schedule_not_due():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    earlier = datetime(2026, 11, 2, tzinfo=timezone.utc)

    assert repo.list_due_schedules(earlier) == []


def test_claim_only_once():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    assert repo.claim_due_schedule("schedule-001", NOW, NOW) is not None
    assert repo.claim_due_schedule("schedule-001", NOW, NOW) is None


def test_save_run_and_history():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    run = BriefingRun(
        id="run-001",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
        result={"text": "Morning briefing completed."},
    )

    token = repo.claim_due_schedule(run.schedule_id, run.scheduled_for, NOW)
    assert token is not None
    repo.save_run(run, token, NOW)

    history = repo.list_history("workspace-a")

    assert len(history) == 1
    assert history[0].status == "success"
    assert repo.list_history("workspace-b") == []


def test_duplicate_run_rejected():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    run = BriefingRun(
        id="run-001",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
    )

    token = repo.claim_due_schedule(run.schedule_id, run.scheduled_for, NOW)
    assert token is not None

    repo.save_run(run, token, NOW)

    with pytest.raises(ValueError, match="already exists"):
        repo.save_run(run, token, NOW)


def test_run_workspace_must_match_schedule():
    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule(workspace_id="workspace-a"))

    invalid_run = BriefingRun(
        id="run-invalid",
        workspace_id="workspace-b",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
    )

    with pytest.raises(ValueError, match="workspace"):
        repo.save_run(invalid_run, "invalid-token", NOW)


def test_claim_expires_and_can_be_recovered():
    """An unfinished claim can be retried after its lease expires."""
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    assert repo.claim_due_schedule("schedule-001", NOW, NOW) is not None

    before_expiry = NOW + timedelta(minutes=3)
    assert repo.claim_due_schedule("schedule-001", NOW, before_expiry) is None

    after_expiry = NOW + timedelta(minutes=5)
    assert repo.claim_due_schedule("schedule-001", NOW, after_expiry) is not None

    assert repo.claim_due_schedule("schedule-001", NOW, after_expiry) is None


def test_expired_token_cannot_save_run():
    """An expired worker cannot save its result."""
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    old_token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert old_token is not None

    run = BriefingRun(
        id="run-old",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
    )

    expired_at = NOW + timedelta(minutes=5)

    with pytest.raises(ValueError, match="expired claim token"):
        repo.save_run(run, old_token, expired_at)


def test_new_token_can_save_after_reclaim():
    """A new worker can save after the previous lease expires."""
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    old_token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert old_token is not None

    reclaim_at = NOW + timedelta(minutes=5)
    new_token = repo.claim_due_schedule("schedule-001", NOW, reclaim_at)

    assert new_token is not None
    assert new_token != old_token

    run = BriefingRun(
        id="run-new",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=reclaim_at,
        status="success",
    )

    with pytest.raises(ValueError, match="Invalid or expired claim token"):
        repo.save_run(run, old_token, reclaim_at)

    repo.save_run(run, new_token, reclaim_at)

    history = repo.list_history("workspace-a")
    assert len(history) == 1
    assert history[0].id == "run-new"


def test_complete_run_saves_history_and_advances_schedule():
    """Completing a run saves history and advances the schedule together."""
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    run = BriefingRun(
        id="run-complete-001",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
        result={"text": "Morning briefing completed."},
    )

    next_run = NOW + timedelta(days=3)

    repo.complete_run(run, token, NOW, next_run)

    saved = repo.get_schedule("workspace-a", "schedule-001")
    history = repo.list_history("workspace-a", "schedule-001")

    assert saved is not None
    assert saved.next_run_at == next_run
    assert len(history) == 1
    assert history[0].id == "run-complete-001"

    # The completed occurrence cannot be claimed again.
    assert repo.claim_due_schedule("schedule-001", NOW, NOW) is None


def test_complete_run_rejects_stale_token():
    """A stale worker cannot complete a reclaimed occurrence."""
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    repo.create_schedule(make_schedule())

    old_token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert old_token is not None

    reclaim_at = NOW + timedelta(minutes=5)
    new_token = repo.claim_due_schedule("schedule-001", NOW, reclaim_at)
    assert new_token is not None

    run = BriefingRun(
        id="run-stale",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=reclaim_at,
        status="success",
    )

    with pytest.raises(ValueError, match="Invalid or expired claim token"):
        repo.complete_run(
            run, old_token, reclaim_at, NOW + timedelta(days=3)
        )

    assert repo.list_history("workspace-a") == []
    assert repo.get_schedule("workspace-a", "schedule-001").next_run_at == NOW


def test_complete_run_rejects_cancelled_schedule():
    """Cancellation prevents an already claimed task from completing."""
    from dataclasses import replace
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    schedule = make_schedule()
    repo.create_schedule(schedule)

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    repo.update_schedule(
        replace(
            schedule,
            enabled=False,
            next_run_at=None,
            cancelled_at=NOW,
        )
    )

    run = BriefingRun(
        id="run-cancelled",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
    )

    with pytest.raises(ValueError, match="no longer eligible|No active claim"):
        repo.complete_run(
            run, token, NOW, NOW + timedelta(days=3)
        )

    assert repo.list_history("workspace-a") == []


def test_complete_run_rejects_modified_schedule_same_next_run():
    """A schedule modification invalidates an already claimed execution."""
    from dataclasses import replace
    from datetime import timedelta

    repo = InMemoryBriefingRepository()
    original = make_schedule()
    repo.create_schedule(original)

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    # The interval changes, but the next execution timestamp stays the same.
    modified = replace(
        original,
        interval_days=7,
        next_run_at=NOW,
        updated_at=NOW + timedelta(seconds=1),
    )
    repo.update_schedule(modified)

    run = BriefingRun(
        id="run-after-modification",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW + timedelta(seconds=2),
        status="success",
    )

    with pytest.raises(ValueError, match="changed|eligible|conflict|No active claim"):
        repo.complete_run(
            run,
            token,
            NOW + timedelta(seconds=2),
            NOW + timedelta(days=3),
        )

    assert repo.list_history("workspace-a") == []
    assert repo.get_schedule("workspace-a", "schedule-001").interval_days == 7
