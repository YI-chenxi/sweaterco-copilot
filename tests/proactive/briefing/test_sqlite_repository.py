"""Tests for SQLite briefing persistence."""

from datetime import date, datetime, time, timezone

from app.agent.proactive.briefing.models import BriefingSchedule
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def make_schedule() -> BriefingSchedule:
    return BriefingSchedule(
        id="schedule-001",
        workspace_id="workspace-a",
        interval_days=3,
        local_time=time(8, 0),
        timezone="Asia/Singapore",
        start_date=date(2026, 11, 3),
        enabled=True,
        next_run_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def test_create_and_get_schedule(tmp_path):
    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)

    repo.create_schedule(make_schedule())
    schedule = repo.get_schedule("workspace-a", "schedule-001")

    assert schedule is not None
    assert schedule.interval_days == 3
    assert schedule.local_time == time(8, 0)
    assert schedule.timezone == "Asia/Singapore"


def test_persistence_after_reopen(tmp_path):
    database = tmp_path / "briefings.db"

    first_repo = SQLiteBriefingRepository(database)
    first_repo.create_schedule(make_schedule())

    second_repo = SQLiteBriefingRepository(database)
    schedule = second_repo.get_schedule("workspace-a", "schedule-001")

    assert schedule is not None
    assert schedule.id == "schedule-001"
    assert schedule.start_date == date(2026, 11, 3)


def test_workspace_isolation(tmp_path):
    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)
    repo.create_schedule(make_schedule())

    assert repo.get_schedule("workspace-b", "schedule-001") is None
    assert repo.get_schedule("workspace-a", "schedule-001") is not None


def test_update_schedule(tmp_path):
    """Updating a schedule persists the new interval."""
    from dataclasses import replace

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    schedule = make_schedule()
    repo.create_schedule(schedule)

    updated = replace(schedule, interval_days=7)
    repo.update_schedule(updated)

    saved = repo.get_schedule("workspace-a", schedule.id)
    assert saved is not None
    assert saved.interval_days == 7


def test_pause_schedule_persists_after_reopen(tmp_path):
    """A paused schedule remains disabled after reopening."""
    from dataclasses import replace

    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)
    schedule = make_schedule()
    repo.create_schedule(schedule)

    repo.update_schedule(replace(schedule, enabled=False))

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("workspace-a", schedule.id)

    assert saved is not None
    assert saved.enabled is False


def test_cross_workspace_update_rejected(tmp_path):
    """A different workspace cannot update an existing schedule."""
    from dataclasses import replace

    import pytest

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    schedule = make_schedule()
    repo.create_schedule(schedule)

    unauthorized = replace(
        schedule,
        workspace_id="workspace-b",
        interval_days=7,
    )

    with pytest.raises(ValueError, match="Schedule not found"):
        repo.update_schedule(unauthorized)

    saved = repo.get_schedule("workspace-a", schedule.id)
    assert saved is not None
    assert saved.interval_days == 3


def test_list_schedules_is_workspace_scoped(tmp_path):
    """Listing schedules must not expose another workspace."""
    from dataclasses import replace

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    first = make_schedule()
    second = replace(
        first,
        id="schedule-002",
        workspace_id="workspace-b",
    )

    repo.create_schedule(first)
    repo.create_schedule(second)

    schedules_a = repo.list_schedules("workspace-a")
    schedules_b = repo.list_schedules("workspace-b")

    assert [item.id for item in schedules_a] == ["schedule-001"]
    assert [item.id for item in schedules_b] == ["schedule-002"]


def test_due_schedule_at_exact_time(tmp_path):
    """A schedule is due at its exact scheduled time."""
    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    due = repo.list_due_schedules(NOW)

    assert len(due) == 1
    assert due[0].id == "schedule-001"


def test_future_schedule_not_due(tmp_path):
    """A future schedule must not execute early."""
    from datetime import timedelta

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    earlier = NOW - timedelta(minutes=1)

    assert repo.list_due_schedules(earlier) == []


def test_paused_schedule_not_due(tmp_path):
    """Disabled schedules must not be returned."""
    from dataclasses import replace

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    schedule = make_schedule()
    repo.create_schedule(replace(schedule, enabled=False))

    assert repo.list_due_schedules(NOW) == []


def test_due_query_rejects_naive_datetime(tmp_path):
    """Due queries require timezone-aware UTC."""
    import pytest

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")

    naive_now = datetime(2026, 11, 3, 0, 0)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        repo.list_due_schedules(naive_now)


def test_sqlite_claim_only_once_across_connections(tmp_path):
    """Two repository instances cannot claim the same occurrence."""
    database = tmp_path / "briefings.db"

    repo_a = SQLiteBriefingRepository(database)
    repo_b = SQLiteBriefingRepository(database)

    repo_a.create_schedule(make_schedule())

    token_a = repo_a.claim_due_schedule("schedule-001", NOW, NOW)
    token_b = repo_b.claim_due_schedule("schedule-001", NOW, NOW)

    assert isinstance(token_a, str)
    assert token_a
    assert token_b is None


def make_run():
    from app.agent.proactive.briefing.models import BriefingRun

    return BriefingRun(
        id="run-001",
        workspace_id="workspace-a",
        schedule_id="schedule-001",
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
        result={"text": "Morning briefing completed."},
    )


def test_sqlite_save_run_with_valid_token(tmp_path):
    """A valid claim token allows saving a run."""
    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    repo.save_run(make_run(), token, NOW)

    with repo._connect() as connection:
        row = connection.execute(
            "SELECT status, result_json FROM briefing_runs"
        ).fetchone()

    assert row is not None
    assert row[0] == "success"

    import json

    assert json.loads(row[1])["text"] == "Morning briefing completed."


def test_sqlite_duplicate_run_rejected(tmp_path):
    """An occurrence cannot be saved twice."""
    import pytest

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    repo.save_run(make_run(), token, NOW)

    with pytest.raises(ValueError, match="already exists"):
        repo.save_run(make_run(), token, NOW)


def test_sqlite_expired_token_rejected(tmp_path):
    """An expired claim cannot save a run."""
    from datetime import timedelta

    import pytest

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    expired_at = NOW + timedelta(minutes=5)

    with pytest.raises(ValueError, match="expired claim token"):
        repo.save_run(make_run(), token, expired_at)


def test_sqlite_list_history(tmp_path):
    """Saved briefing runs can be retrieved through the repository."""
    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None
    repo.save_run(make_run(), token, NOW)

    history = repo.list_history("workspace-a")

    assert len(history) == 1
    assert history[0].id == "run-001"
    assert history[0].status == "success"
    assert history[0].result["text"] == "Morning briefing completed."


def test_sqlite_history_persists_after_reopen(tmp_path):
    """Execution history survives repository recreation."""
    database = tmp_path / "briefings.db"

    first_repo = SQLiteBriefingRepository(database)
    first_repo.create_schedule(make_schedule())

    token = first_repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None
    first_repo.save_run(make_run(), token, NOW)

    second_repo = SQLiteBriefingRepository(database)
    history = second_repo.list_history("workspace-a", "schedule-001")

    assert len(history) == 1
    assert history[0].scheduled_for == NOW
    assert history[0].result["text"] == "Morning briefing completed."


def test_sqlite_history_workspace_isolation(tmp_path):
    """Another workspace cannot access briefing history."""
    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None
    repo.save_run(make_run(), token, NOW)

    assert repo.list_history("workspace-b") == []
    assert repo.list_history("workspace-b", "schedule-001") == []
    assert len(repo.list_history("workspace-a", "schedule-001")) == 1


def test_sqlite_rejects_cross_workspace_run(tmp_path):
    """A run cannot be saved under another workspace."""
    from dataclasses import replace

    import pytest

    repo = SQLiteBriefingRepository(tmp_path / "briefings.db")
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    invalid_run = replace(
        make_run(),
        workspace_id="workspace-b",
    )

    with pytest.raises(ValueError, match="workspace"):
        repo.save_run(invalid_run, token, NOW)

    assert repo.list_history("workspace-a") == []
    assert repo.list_history("workspace-b") == []


def test_sqlite_lease_recovery_and_old_token_rejection(tmp_path):
    """Expired claims can be recovered without accepting stale tokens."""
    from datetime import timedelta

    import pytest

    database = tmp_path / "briefings.db"
    repo_a = SQLiteBriefingRepository(database)
    repo_b = SQLiteBriefingRepository(database)

    repo_a.create_schedule(make_schedule())

    old_token = repo_a.claim_due_schedule("schedule-001", NOW, NOW)
    assert old_token is not None

    before_expiry = NOW + timedelta(minutes=3)
    assert repo_b.claim_due_schedule("schedule-001", NOW, before_expiry) is None

    after_expiry = NOW + timedelta(minutes=5)
    new_token = repo_b.claim_due_schedule("schedule-001", NOW, after_expiry)

    assert new_token is not None
    assert new_token != old_token

    with pytest.raises(ValueError, match="Invalid or expired claim token"):
        repo_a.save_run(make_run(), old_token, after_expiry)

    repo_b.save_run(make_run(), new_token, after_expiry)

    history = repo_a.list_history("workspace-a")
    assert len(history) == 1
    assert history[0].status == "success"

    assert repo_a.claim_due_schedule("schedule-001", NOW, after_expiry) is None


def test_sqlite_concurrent_claim_only_one_wins(tmp_path):
    """Two concurrent workers must not claim the same occurrence."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)
    repo.create_schedule(make_schedule())

    barrier = Barrier(2)

    def worker():
        worker_repo = SQLiteBriefingRepository(database)
        barrier.wait(timeout=5)
        return worker_repo.claim_due_schedule("schedule-001", NOW, NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]

    successful_tokens = [token for token in results if token is not None]

    assert len(successful_tokens) == 1
    assert results.count(None) == 1


def test_cancelled_schedule_persists_after_reopen(tmp_path):
    """A cancelled schedule remains cancelled after reopening SQLite."""
    from dataclasses import replace

    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)
    schedule = make_schedule()
    repo.create_schedule(schedule)

    cancelled = replace(
        schedule,
        enabled=False,
        next_run_at=None,
        cancelled_at=NOW,
        updated_at=NOW,
    )
    repo.update_schedule(cancelled)

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("workspace-a", schedule.id)

    assert saved is not None
    assert saved.enabled is False
    assert saved.next_run_at is None
    assert saved.cancelled_at == NOW
    assert reopened.list_due_schedules(NOW) == []


def test_legacy_sqlite_database_migrates_cancelled_at(tmp_path):
    """An existing database gains the new column without losing data."""
    import sqlite3

    database = tmp_path / "legacy.db"

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE briefing_schedules (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                interval_days INTEGER NOT NULL,
                local_time TEXT NOT NULL,
                timezone TEXT NOT NULL,
                start_date TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                next_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO briefing_schedules VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-001",
                "workspace-a",
                3,
                "08:00",
                "Asia/Singapore",
                "2026-11-03",
                1,
                NOW.isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

    repo = SQLiteBriefingRepository(database)
    saved = repo.get_schedule("workspace-a", "legacy-001")

    assert saved is not None
    assert saved.interval_days == 3
    assert saved.cancelled_at is None

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(briefing_schedules)"
            )
        }

    assert "cancelled_at" in columns


def test_sqlite_complete_run_advances_schedule(tmp_path):
    """Completing a run persists history and advances the schedule."""
    from datetime import timedelta

    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    next_run = NOW + timedelta(days=3)

    repo.complete_run(
        make_run(),
        token,
        NOW,
        next_run,
    )

    # Reopen the database to verify persistence.
    reopened = SQLiteBriefingRepository(database)

    saved = reopened.get_schedule("workspace-a", "schedule-001")
    history = reopened.list_history("workspace-a", "schedule-001")

    assert saved is not None
    assert saved.next_run_at == next_run
    assert len(history) == 1
    assert history[0].status == "success"
    assert history[0].result["text"] == "Morning briefing completed."

    # The completed occurrence must not be claimed again.
    assert reopened.claim_due_schedule("schedule-001", NOW, NOW) is None


def test_sqlite_complete_run_rolls_back_on_update_failure(tmp_path):
    """A failed schedule update rolls back the entire completion."""
    from datetime import timedelta

    import pytest

    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)
    repo.create_schedule(make_schedule())

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    with repo._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_briefing_schedule_update
            BEFORE UPDATE ON briefing_schedules
            BEGIN
                SELECT RAISE(ABORT, 'Simulated schedule update failure');
            END;
            """
        )

    with pytest.raises(Exception, match="Simulated schedule update failure"):
        repo.complete_run(
            make_run(),
            token,
            NOW,
            NOW + timedelta(days=3),
        )

    reopened = SQLiteBriefingRepository(database)

    # The run insert must have been rolled back.
    assert reopened.list_history("workspace-a") == []

    # The original schedule must remain unchanged.
    saved = reopened.get_schedule("workspace-a", "schedule-001")
    assert saved is not None
    assert saved.next_run_at == NOW

    # The original claim should still exist after rollback.
    with reopened._connect() as connection:
        claim_count = connection.execute(
            "SELECT COUNT(*) FROM briefing_claims"
        ).fetchone()[0]

    assert claim_count == 1


def test_sqlite_stale_update_cannot_restore_cancelled_schedule(tmp_path):
    """An outdated update cannot overwrite a completed cancellation."""
    from dataclasses import replace

    import pytest

    database = tmp_path / "briefings.db"
    repo_a = SQLiteBriefingRepository(database)
    repo_b = SQLiteBriefingRepository(database)

    original = make_schedule()
    repo_a.create_schedule(original)

    # A reads the schedule before cancellation.
    stale_schedule = repo_a.get_schedule("workspace-a", original.id)
    assert stale_schedule is not None

    # B cancels the schedule.
    cancelled = replace(
        original,
        enabled=False,
        next_run_at=None,
        cancelled_at=NOW,
        updated_at=NOW,
    )
    repo_b.update_schedule(cancelled)

    # A attempts to save an outdated version.
    outdated_update = replace(stale_schedule, interval_days=7)

    with pytest.raises(ValueError, match="cancelled|conflict"):
        repo_a.update_schedule(outdated_update)

    saved = repo_a.get_schedule("workspace-a", original.id)

    assert saved is not None
    assert saved.enabled is False
    assert saved.cancelled_at == NOW
    assert saved.next_run_at is None


def test_sqlite_update_invalidates_existing_claim(tmp_path):
    """Updating a schedule invalidates claims held by old workers."""
    from dataclasses import replace
    from datetime import timedelta

    import pytest

    database = tmp_path / "briefings.db"
    repo_a = SQLiteBriefingRepository(database)
    repo_b = SQLiteBriefingRepository(database)

    original = make_schedule()
    repo_a.create_schedule(original)

    old_token = repo_a.claim_due_schedule("schedule-001", NOW, NOW)
    assert old_token is not None

    modified = replace(
        original,
        interval_days=7,
        next_run_at=NOW,
        updated_at=NOW + timedelta(seconds=1),
    )
    repo_b.update_schedule(modified)

    with pytest.raises(ValueError, match="No active claim|eligible|token"):
        repo_a.complete_run(
            make_run(),
            old_token,
            NOW + timedelta(seconds=2),
            NOW + timedelta(days=3),
        )

    saved = repo_a.get_schedule("workspace-a", "schedule-001")
    assert saved is not None
    assert saved.interval_days == 7
    assert saved.next_run_at == NOW
    assert repo_a.list_history("workspace-a") == []


def test_sqlite_failed_update_preserves_claim(tmp_path):
    """A failed schedule update must preserve the existing claim."""
    import sqlite3
    from dataclasses import replace
    from datetime import timedelta

    import pytest

    database = tmp_path / "briefings.db"
    repo = SQLiteBriefingRepository(database)

    original = make_schedule()
    repo.create_schedule(original)

    token = repo.claim_due_schedule("schedule-001", NOW, NOW)
    assert token is not None

    with repo._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_schedule_update
            BEFORE UPDATE ON briefing_schedules
            BEGIN
                SELECT RAISE(ABORT, 'Simulated update failure');
            END;
            """
        )

    modified = replace(
        original,
        interval_days=7,
        updated_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(sqlite3.DatabaseError, match="Simulated update failure"):
        repo.update_schedule(modified)

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("workspace-a", "schedule-001")

    assert saved is not None
    assert saved.interval_days == 3

    with reopened._connect() as connection:
        claim = connection.execute(
            """
            SELECT claim_token
            FROM briefing_claims
            WHERE schedule_id = ?
            """,
            ("schedule-001",),
        ).fetchone()

    assert claim is not None
    assert claim[0] == token
