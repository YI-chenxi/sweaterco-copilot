"""Tests for confirmed briefing update and cancel operations."""

from app.agent.proactive.briefing.service import BriefingService
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository


def setup_schedule(tmp_path):
    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    service = BriefingService(repo)

    created = service.create_schedule(
        "group02",
        {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    )

    assert created["ok"] is True
    return repo, created["id"]


def test_confirmed_update_changes_schedule_and_version(tmp_path):
    repo, schedule_id = setup_schedule(tmp_path)

    command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
    }

    receipt = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="update-test-001",
        expected_version="1",
    )

    saved = repo.get_schedule("group02", schedule_id)

    assert receipt["ok"] is True
    assert receipt["status"] == "updated"
    assert saved.interval_days == 3
    assert saved.config_version == 2


def test_confirmed_cancel_disables_schedule(tmp_path):
    repo, schedule_id = setup_schedule(tmp_path)

    command = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": schedule_id,
        "parameters": {},
    }

    receipt = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="cancel-test-001",
        expected_version="1",
    )

    saved = repo.get_schedule("group02", schedule_id)

    assert receipt["ok"] is True
    assert receipt["status"] == "cancelled"
    assert saved.enabled is False
    assert saved.next_run_at is None
    assert saved.cancelled_at is not None
    assert saved.config_version == 2


def test_duplicate_update_returns_same_receipt(tmp_path):
    repo, schedule_id = setup_schedule(tmp_path)

    command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
    }

    first = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="duplicate-update-001",
        expected_version="1",
    )

    second = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="duplicate-update-001",
        expected_version="1",
    )

    saved = repo.get_schedule("group02", schedule_id)

    assert first == second
    assert saved.interval_days == 3
    assert saved.config_version == 2


def test_stale_update_cannot_overwrite_new_version(tmp_path):
    import pytest

    repo, schedule_id = setup_schedule(tmp_path)

    command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
    }

    repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="first-update-001",
        expected_version="1",
    )

    stale_command = {
        **command,
        "parameters": {"interval_days": 7},
    }

    with pytest.raises(ValueError, match="CONFLICT"):
        repo.execute_confirmed_action(
            "group02",
            stale_command,
            idempotency_key="stale-update-001",
            expected_version="1",
        )

    saved = repo.get_schedule("group02", schedule_id)

    assert saved.interval_days == 3
    assert saved.config_version == 2


def test_duplicate_cancel_returns_same_receipt(tmp_path):
    repo, schedule_id = setup_schedule(tmp_path)

    command = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": schedule_id,
        "parameters": {},
    }

    first = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="duplicate-cancel-001",
        expected_version="1",
    )

    second = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="duplicate-cancel-001",
        expected_version="1",
    )

    saved = repo.get_schedule("group02", schedule_id)

    assert first == second
    assert first["status"] == "cancelled"
    assert saved.enabled is False
    assert saved.cancelled_at is not None
    assert saved.config_version == 2


def test_stale_cancel_cannot_cancel_modified_schedule(tmp_path):
    import pytest

    repo, schedule_id = setup_schedule(tmp_path)

    update_command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
    }

    repo.execute_confirmed_action(
        "group02",
        update_command,
        idempotency_key="update-before-cancel",
        expected_version="1",
    )

    cancel_command = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": schedule_id,
        "parameters": {},
    }

    with pytest.raises(ValueError, match="CONFLICT"):
        repo.execute_confirmed_action(
            "group02",
            cancel_command,
            idempotency_key="stale-cancel-001",
            expected_version="1",
        )

    saved = repo.get_schedule("group02", schedule_id)

    assert saved.interval_days == 3
    assert saved.enabled is True
    assert saved.cancelled_at is None
    assert saved.config_version == 2


def test_cross_workspace_cancel_cannot_change_schedule(tmp_path):
    import pytest

    repo, schedule_id = setup_schedule(tmp_path)

    command = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": schedule_id,
        "parameters": {},
    }

    with pytest.raises(ValueError, match="NOT_FOUND"):
        repo.execute_confirmed_action(
            "another-workspace",
            command,
            idempotency_key="cross-workspace-cancel",
            expected_version="1",
        )

    saved = repo.get_schedule("group02", schedule_id)
    assert saved.enabled is True
    assert saved.cancelled_at is None
    assert saved.config_version == 1


def test_update_receipt_failure_rolls_back_mutation(tmp_path):
    import sqlite3

    import pytest

    repo, schedule_id = setup_schedule(tmp_path)

    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("""
            CREATE TRIGGER fail_update_receipt
            BEFORE INSERT ON briefing_action_receipts
            BEGIN
                SELECT RAISE(ABORT, 'simulated failure');
            END;
        """)

    command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
    }

    with pytest.raises(sqlite3.DatabaseError):
        repo.execute_confirmed_action(
            "group02",
            command,
            idempotency_key="rollback-update-001",
            expected_version="1",
        )

    saved = repo.get_schedule("group02", schedule_id)

    assert saved.interval_days == 1
    assert saved.config_version == 1

    with sqlite3.connect(repo.database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts"
        ).fetchone()[0]

    assert count == 0
