"""Tests for the scheduled briefing service."""

from datetime import datetime, timezone

import pytest

from app.agent.proactive.briefing.memory_repository import InMemoryBriefingRepository
from app.agent.proactive.briefing.service import BriefingService

NOW = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)


def make_service():
    """Create a service with a fixed clock for reproducible tests."""
    return BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: NOW,
    )


def valid_parameters():
    return {
        "interval_days": 3,
        "local_time": "08:00",
        "timezone": "Asia/Singapore",
        "start_date": "2026-11-04",
        "enabled": True,
    }


def test_valid_parameters():
    result = make_service()._validate_parameters(valid_parameters())

    assert result["interval_days"] == 3
    assert result["local_time"].hour == 8
    assert result["timezone"] == "Asia/Singapore"
    assert result["enabled"] is True


@pytest.mark.parametrize("interval", [0, -3, True, 1.5])
def test_invalid_interval(interval):
    parameters = valid_parameters()
    parameters["interval_days"] = interval

    with pytest.raises(ValueError, match="positive integer"):
        make_service()._validate_parameters(parameters)


def test_invalid_local_time():
    parameters = valid_parameters()
    parameters["local_time"] = "25:00"

    with pytest.raises(ValueError, match="HH:MM"):
        make_service()._validate_parameters(parameters)


def test_invalid_timezone():
    parameters = valid_parameters()
    parameters["timezone"] = "Invalid/Timezone"

    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        make_service()._validate_parameters(parameters)


def test_missing_start_date():
    parameters = valid_parameters()
    del parameters["start_date"]

    with pytest.raises(ValueError, match="ISO date"):
        make_service()._validate_parameters(parameters)


def test_invalid_enabled():
    parameters = valid_parameters()
    parameters["enabled"] = "yes"

    with pytest.raises(ValueError, match="boolean"):
        make_service()._validate_parameters(parameters)


def test_create_schedule_success():
    """A valid request creates a schedule."""
    service = make_service()

    result = service.create_schedule("workspace-a", valid_parameters())

    assert result["ok"] is True
    assert result["status"] == "created"
    assert result["id"] is not None
    assert result["error_code"] is None

    saved = service.repository.get_schedule("workspace-a", result["id"])
    assert saved is not None
    assert saved.interval_days == 3


def test_create_schedule_next_run():
    """Singapore 08:00 is stored as midnight UTC."""
    service = make_service()

    result = service.create_schedule("workspace-a", valid_parameters())

    assert result["ok"] is True
    assert result["data"]["next_run_at"] == "2026-11-04T00:00:00+00:00"


def test_create_disabled_schedule():
    """A disabled schedule has no next execution time."""
    service = make_service()
    parameters = valid_parameters()
    parameters["enabled"] = False

    result = service.create_schedule("workspace-a", parameters)

    assert result["ok"] is True
    assert result["data"]["enabled"] is False
    assert result["data"]["next_run_at"] is None


def test_create_schedule_invalid_workspace():
    """An empty workspace identifier is rejected."""
    service = make_service()

    result = service.create_schedule("", valid_parameters())

    assert result["ok"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert service.repository.list_schedules("") == []


def test_create_schedule_sqlite_persistence(tmp_path):
    """A schedule survives reopening its SQLite database."""
    from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository

    database = tmp_path / "briefings.db"
    service = BriefingService(
        repository=SQLiteBriefingRepository(database),
        clock=lambda: NOW,
    )

    result = service.create_schedule("workspace-a", valid_parameters())
    assert result["ok"] is True

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("workspace-a", result["id"])

    assert saved is not None
    assert saved.interval_days == 3
    assert saved.next_run_at == NOW


def test_create_schedule_storage_error():
    """Repository failures must not be reported as invalid user input."""
    from unittest.mock import Mock

    repository = Mock()
    repository.create_schedule.side_effect = ValueError("Database write failed")

    service = BriefingService(
        repository=repository,
        clock=lambda: NOW,
    )

    result = service.create_schedule("workspace-a", valid_parameters())

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["error_code"] == "STORAGE_ERROR"
    assert result["id"] is None
    assert result["data"] == {}


def test_create_schedule_rejects_non_utc_clock():
    """A non-UTC clock must not create a schedule."""
    from datetime import timedelta

    service = BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: datetime(
            2026, 11, 4, 8, 0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    result = service.create_schedule("workspace-a", valid_parameters())

    assert result["ok"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert service.repository.list_schedules("workspace-a") == []


def test_create_disabled_schedule_rejects_non_utc_clock():
    """A disabled schedule must also reject a non-UTC clock."""
    from datetime import timedelta

    service = BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: datetime(
            2026, 11, 4, 8, 0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    parameters = valid_parameters()
    parameters["enabled"] = False

    result = service.create_schedule("workspace-a", parameters)

    assert result["ok"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert service.repository.list_schedules("workspace-a") == []


def test_update_schedule_interval():
    """Updating an interval recalculates the next execution."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    result = service.update_schedule(
        "workspace-a",
        created["id"],
        {"interval_days": 7},
    )

    assert result["ok"] is True
    assert result["status"] == "updated"

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.interval_days == 7
    assert saved.created_at.isoformat() == created["data"]["created_at"]


def test_update_schedule_pause():
    """Pausing a schedule clears its next execution time."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    result = service.update_schedule(
        "workspace-a",
        created["id"],
        {"enabled": False},
    )

    assert result["ok"] is True

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.enabled is False
    assert saved.next_run_at is None


def test_update_schedule_resume():
    """A paused schedule can be resumed."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    schedule_id = created["id"]

    paused = service.update_schedule(
        "workspace-a", schedule_id, {"enabled": False}
    )
    assert paused["ok"] is True

    resumed = service.update_schedule(
        "workspace-a", schedule_id, {"enabled": True}
    )

    assert resumed["ok"] is True
    assert resumed["data"]["enabled"] is True
    assert resumed["data"]["next_run_at"] == NOW.isoformat()


def test_update_schedule_local_time():
    """Changing local time recalculates next execution."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())

    result = service.update_schedule(
        "workspace-a", created["id"], {"local_time": "09:00"}
    )

    assert result["ok"] is True
    assert result["data"]["local_time"] == "09:00"
    assert result["data"]["next_run_at"] == "2026-11-04T01:00:00+00:00"


def test_update_schedule_cross_workspace_rejected():
    """Another workspace cannot modify a schedule."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())

    result = service.update_schedule(
        "workspace-b", created["id"], {"interval_days": 7}
    )

    assert result["ok"] is False
    assert result["error_code"] == "NOT_FOUND"

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.interval_days == 3


def test_update_schedule_invalid_interval():
    """Invalid updates must not change the stored schedule."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())

    result = service.update_schedule(
        "workspace-a", created["id"], {"interval_days": -1}
    )

    assert result["ok"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.interval_days == 3


def test_cancel_schedule_success():
    """Cancelling a schedule preserves it but disables execution."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    schedule_id = created["id"]

    result = service.cancel_schedule("workspace-a", schedule_id)

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert result["id"] == schedule_id

    saved = service.repository.get_schedule("workspace-a", schedule_id)
    assert saved is not None
    assert saved.enabled is False
    assert saved.next_run_at is None
    assert saved.cancelled_at == NOW


def test_cancel_schedule_cannot_resume():
    """A cancelled schedule cannot be re-enabled."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    schedule_id = created["id"]

    service.cancel_schedule("workspace-a", schedule_id)

    result = service.update_schedule(
        "workspace-a", schedule_id, {"enabled": True}
    )

    assert result["ok"] is False
    assert result["error_code"] == "CONFLICT"


def test_cancel_schedule_cross_workspace_rejected():
    """Another workspace cannot cancel a schedule."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())

    result = service.cancel_schedule("workspace-b", created["id"])

    assert result["ok"] is False
    assert result["error_code"] == "NOT_FOUND"

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.enabled is True
    assert saved.cancelled_at is None


def test_cancel_schedule_sqlite_persistence(tmp_path):
    """Cancellation survives reopening SQLite and prevents resuming."""
    from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository

    database = tmp_path / "briefings.db"

    service = BriefingService(
        repository=SQLiteBriefingRepository(database),
        clock=lambda: NOW,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True
    schedule_id = created["id"]

    cancelled = service.cancel_schedule("workspace-a", schedule_id)
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"

    reopened = SQLiteBriefingRepository(database)
    saved = reopened.get_schedule("workspace-a", schedule_id)

    assert saved is not None
    assert saved.enabled is False
    assert saved.next_run_at is None
    assert saved.cancelled_at == NOW
    assert reopened.list_due_schedules(NOW) == []

    restarted_service = BriefingService(
        repository=reopened,
        clock=lambda: NOW,
    )

    resumed = restarted_service.update_schedule(
        "workspace-a", schedule_id, {"enabled": True}
    )

    assert resumed["ok"] is False
    assert resumed["error_code"] == "CONFLICT"


def test_list_schedules_workspace_isolation():
    """Each workspace only sees its own schedules."""
    service = make_service()

    first = service.create_schedule("workspace-a", valid_parameters())
    second = service.create_schedule("workspace-b", valid_parameters())

    schedules_a = service.list_schedules("workspace-a")
    schedules_b = service.list_schedules("workspace-b")

    assert len(schedules_a) == 1
    assert len(schedules_b) == 1
    assert schedules_a[0]["id"] == first["id"]
    assert schedules_b[0]["id"] == second["id"]


def test_list_schedules_includes_cancelled():
    """Cancelled schedules remain visible in the list."""
    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())

    cancelled = service.cancel_schedule("workspace-a", created["id"])
    assert cancelled["ok"] is True

    schedules = service.list_schedules("workspace-a")

    assert len(schedules) == 1
    assert schedules[0]["enabled"] is False
    assert schedules[0]["cancelled_at"] == NOW.isoformat()
    assert schedules[0]["next_run_at"] is None


def test_list_schedules_json_serializable():
    """Schedule data can be serialized for API and UI integration."""
    import json

    service = make_service()
    service.create_schedule("workspace-a", valid_parameters())

    schedules = service.list_schedules("workspace-a")

    encoded = json.dumps(schedules)
    decoded = json.loads(encoded)

    assert decoded[0]["interval_days"] == 3
    assert decoded[0]["local_time"] == "08:00"
    assert decoded[0]["start_date"] == "2026-11-04"


def test_list_history_empty():
    """A workspace without runs has no briefing history."""
    service = make_service()

    assert service.list_history("workspace-a") == []


def test_list_history_returns_saved_run():
    """Service returns JSON-compatible execution history."""
    import json

    from app.agent.proactive.briefing.models import BriefingRun

    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    schedule_id = created["id"]

    token = service.repository.claim_due_schedule(schedule_id, NOW, NOW)
    assert token is not None

    run = BriefingRun(
        id="run-001",
        workspace_id="workspace-a",
        schedule_id=schedule_id,
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
        result={
            "text": "Morning briefing completed.",
            "evidence": [{"source": "orders", "reference": "001", "detail": "Risk"}],
            "warnings": [],
            "assumptions": [],
        },
    )
    service.repository.save_run(run, token, NOW)

    history = service.list_history("workspace-a", schedule_id)

    assert len(history) == 1
    assert history[0]["status"] == "success"
    assert history[0]["result"]["text"] == "Morning briefing completed."
    assert history[0]["scheduled_for"] == NOW.isoformat()

    json.dumps(history)


def test_list_history_workspace_isolation():
    """Another workspace cannot access saved briefing history."""
    from app.agent.proactive.briefing.models import BriefingRun

    service = make_service()
    created = service.create_schedule("workspace-a", valid_parameters())
    schedule_id = created["id"]

    token = service.repository.claim_due_schedule(schedule_id, NOW, NOW)
    assert token is not None

    run = BriefingRun(
        id="run-002",
        workspace_id="workspace-a",
        schedule_id=schedule_id,
        scheduled_for=NOW,
        executed_at=NOW,
        status="success",
        result={"text": "Private briefing"},
    )
    service.repository.save_run(run, token, NOW)

    assert service.list_history("workspace-b") == []
    assert service.list_history("workspace-b", schedule_id) == []
    assert len(service.list_history("workspace-a", schedule_id)) == 1


def test_run_due_briefings_success():
    """A due schedule generates a briefing and advances its next run."""
    from datetime import timedelta

    calls = []

    def fake_executor(reference_date):
        calls.append(reference_date)
        return {
            "text": "Morning briefing completed.",
            "evidence": [],
            "warnings": [],
            "assumptions": [],
            "metadata": {"intent": "morning_briefing"},
            "reference_date": reference_date.isoformat(),
        }

    service = BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: NOW,
        briefing_executor=fake_executor,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    results = service.run_due_briefings(NOW)

    assert len(results) == 1
    assert results[0]["status"] == "success"
    assert calls == [NOW.date()]

    history = service.list_history("workspace-a", created["id"])
    assert len(history) == 1
    assert history[0]["status"] == "success"
    assert history[0]["result"]["text"] == "Morning briefing completed."

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.next_run_at == NOW + timedelta(days=3)


def test_run_due_briefings_records_failure():
    """A failed briefing execution must be recorded honestly."""
    from datetime import timedelta

    def failing_executor(reference_date):
        raise RuntimeError("Simulated business tool failure")

    service = BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: NOW,
        briefing_executor=failing_executor,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    results = service.run_due_briefings(NOW)

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["status"] == "failed"
    assert results[0]["error_code"] == "EXECUTION_ERROR"

    history = service.list_history("workspace-a", created["id"])

    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert history[0]["result"] == {}
    assert history[0]["error"] is not None

    saved = service.repository.get_schedule("workspace-a", created["id"])
    assert saved.next_run_at == NOW + timedelta(days=3)


def test_run_due_briefings_is_idempotent():
    """Repeated scheduler calls must not execute the same occurrence twice."""
    calls = []

    def fake_executor(reference_date):
        calls.append(reference_date)
        return {
            "text": "Morning briefing completed.",
            "evidence": [],
            "warnings": [],
            "assumptions": [],
            "metadata": {"intent": "morning_briefing"},
            "reference_date": reference_date.isoformat(),
        }

    service = BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: NOW,
        briefing_executor=fake_executor,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    first = service.run_due_briefings(NOW)
    second = service.run_due_briefings(NOW)

    assert len(first) == 1
    assert first[0]["status"] == "success"
    assert second == []

    assert len(calls) == 1

    history = service.list_history("workspace-a", created["id"])
    assert len(history) == 1


def test_run_due_briefings_sqlite_end_to_end(tmp_path):
    """Scheduled briefing execution survives SQLite repository recreation."""
    from datetime import timedelta

    from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository

    database = tmp_path / "briefings.db"
    calls = []

    def fake_executor(reference_date):
        calls.append(reference_date)
        return {
            "text": "Scheduled morning briefing completed.",
            "evidence": [
                {
                    "source": "orders",
                    "reference": "ORD-001",
                    "detail": "Delivery risk",
                }
            ],
            "warnings": ["Static dataset used."],
            "assumptions": [],
            "metadata": {
                "intent": "morning_briefing",
                "structured_result": {"findings": [{"severity_score": 7}]},
            },
            "reference_date": reference_date.isoformat(),
        }

    service = BriefingService(
        repository=SQLiteBriefingRepository(database),
        clock=lambda: NOW,
        briefing_executor=fake_executor,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    results = service.run_due_briefings(NOW)

    assert len(results) == 1
    assert results[0]["status"] == "success"
    assert len(calls) == 1

    # Reopen SQLite to verify persisted results.
    reopened = SQLiteBriefingRepository(database)

    saved = reopened.get_schedule("workspace-a", created["id"])
    history = reopened.list_history("workspace-a", created["id"])

    assert saved is not None
    assert saved.next_run_at == NOW + timedelta(days=3)

    assert len(history) == 1
    assert history[0].status == "success"
    assert history[0].result["text"] == "Scheduled morning briefing completed."
    assert history[0].result["evidence"][0]["reference"] == "ORD-001"
    assert history[0].result["warnings"] == ["Static dataset used."]
    assert history[0].result["metadata"]["structured_result"]["findings"][0]["severity_score"] == 7

    # Restarted service must not repeat the completed occurrence.
    restarted_service = BriefingService(
        repository=reopened,
        clock=lambda: NOW,
        briefing_executor=fake_executor,
    )

    assert restarted_service.run_due_briefings(NOW) == []
    assert len(calls) == 1


def test_run_due_briefings_reports_storage_failure():
    """A database completion failure must not be silently ignored."""
    import sqlite3

    from app.agent.proactive.briefing.memory_repository import InMemoryBriefingRepository

    class FailingRepository(InMemoryBriefingRepository):
        def complete_run(self, run, claim_token, now, next_run_at):
            raise sqlite3.OperationalError("Simulated database write failure")

    def fake_executor(reference_date):
        return {
            "text": "Morning briefing completed.",
            "evidence": [],
            "warnings": [],
            "assumptions": [],
            "metadata": {"intent": "morning_briefing"},
            "reference_date": reference_date.isoformat(),
        }

    service = BriefingService(
        repository=FailingRepository(),
        clock=lambda: NOW,
        briefing_executor=fake_executor,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    assert created["ok"] is True

    results = service.run_due_briefings(NOW)

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["error_code"] == "STORAGE_ERROR"
    assert service.list_history("workspace-a", created["id"]) == []


def test_run_due_briefings_uses_local_reference_date():
    """The briefing date follows the schedule timezone, not UTC."""
    from datetime import date, datetime, timezone

    execution_time = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)
    received_dates = []

    def fake_executor(reference_date):
        received_dates.append(reference_date)
        return {
            "text": "Morning briefing completed.",
            "evidence": [],
            "warnings": [],
            "assumptions": [],
            "metadata": {"intent": "morning_briefing"},
            "reference_date": reference_date.isoformat(),
        }

    service = BriefingService(
        repository=InMemoryBriefingRepository(),
        clock=lambda: execution_time,
        briefing_executor=fake_executor,
    )

    parameters = valid_parameters()
    parameters["start_date"] = "2026-11-05"
    parameters["local_time"] = "06:00"
    parameters["interval_days"] = 1

    created = service.create_schedule("workspace-a", parameters)
    assert created["ok"] is True

    results = service.run_due_briefings(execution_time)

    assert len(results) == 1
    assert results[0]["status"] == "success"
    assert received_dates == [date(2026, 11, 5)]


def test_run_due_briefings_recovers_after_storage_failure():
    """An unfinished occurrence can recover after its lease expires."""
    from datetime import timedelta

    class FailOnceRepository(InMemoryBriefingRepository):
        def __init__(self):
            super().__init__()
            self.fail_next_completion = True

        def complete_run(self, run, claim_token, now, next_run_at):
            if self.fail_next_completion:
                self.fail_next_completion = False
                raise RuntimeError("Simulated temporary storage failure")
            return super().complete_run(run, claim_token, now, next_run_at)

    calls = []

    def fake_executor(reference_date):
        calls.append(reference_date)
        return {
            "text": "Morning briefing completed.",
            "evidence": [],
            "warnings": [],
            "assumptions": [],
            "metadata": {"intent": "morning_briefing"},
            "reference_date": reference_date.isoformat(),
        }

    repository = FailOnceRepository()
    service = BriefingService(
        repository=repository,
        clock=lambda: NOW,
        briefing_executor=fake_executor,
    )

    created = service.create_schedule("workspace-a", valid_parameters())
    schedule_id = created["id"]

    first = service.run_due_briefings(NOW)
    assert len(first) == 1
    assert first[0]["error_code"] == "STORAGE_ERROR"

    # The lease has not expired yet.
    before_expiry = NOW + timedelta(minutes=3)
    assert service.run_due_briefings(before_expiry) == []

    # The lease expires after five minutes.
    after_expiry = NOW + timedelta(minutes=5)
    recovered = service.run_due_briefings(after_expiry)

    assert len(recovered) == 1
    assert recovered[0]["status"] == "success"

    history = service.list_history("workspace-a", schedule_id)
    assert len(history) == 1
    assert history[0]["status"] == "success"

    # Analysis ran twice, but only one result was persisted.
    assert len(calls) == 2
