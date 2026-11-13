"""Persistent idempotency tests for confirmed briefing actions."""

import sqlite3

from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository


def test_duplicate_create_returns_original_receipt(tmp_path):
    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")

    command = {
        "task_type": "briefing",
        "command_type": "create",
        "target_id": None,
        "parameters": {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    }

    first = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="proposal-001",
        expected_version=None,
    )

    second = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="proposal-001",
        expected_version=None,
    )

    assert first == second
    assert first["ok"] is True

    with sqlite3.connect(repo.database_path) as connection:
        schedule_count = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules "
            "WHERE workspace_id = ?",
            ("group02",),
        ).fetchone()[0]

        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts "
            "WHERE workspace_id = ?",
            ("group02",),
        ).fetchone()[0]

    assert schedule_count == 1
    assert receipt_count == 1


def test_failed_receipt_write_rolls_back_schedule(tmp_path):
    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")

    command = {
        "task_type": "briefing",
        "command_type": "create",
        "target_id": None,
        "parameters": {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    }

    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("""
            CREATE TRIGGER fail_receipt_insert
            BEFORE INSERT ON briefing_action_receipts
            BEGIN
                SELECT RAISE(ABORT, 'simulated receipt failure');
            END;
        """)

    import pytest

    with pytest.raises(sqlite3.DatabaseError):
        repo.execute_confirmed_action(
            "group02",
            command,
            idempotency_key="proposal-rollback-test",
            expected_version=None,
        )

    with sqlite3.connect(repo.database_path) as connection:
        schedule_count = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts"
        ).fetchone()[0]

    assert schedule_count == 0
    assert receipt_count == 0


def test_duplicate_create_after_repository_restart(tmp_path):
    database = tmp_path / "briefing.db"
    repo = SQLiteBriefingRepository(database)

    command = {
        "task_type": "briefing",
        "command_type": "create",
        "target_id": None,
        "parameters": {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    }

    first = repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="restart-test",
        expected_version=None,
    )

    reopened = SQLiteBriefingRepository(database)

    second = reopened.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="restart-test",
        expected_version=None,
    )

    assert first == second

    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

    assert count == 1


def test_same_key_different_command_is_rejected(tmp_path):
    import pytest

    repo = SQLiteBriefingRepository(tmp_path / "briefing.db")

    command = {
        "task_type": "briefing",
        "command_type": "create",
        "target_id": None,
        "parameters": {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    }

    repo.execute_confirmed_action(
        "group02",
        command,
        idempotency_key="conflict-test",
        expected_version=None,
    )

    changed_command = {
        **command,
        "parameters": {
            **command["parameters"],
            "interval_days": 3,
        },
    }

    with pytest.raises(ValueError, match="CONFLICT"):
        repo.execute_confirmed_action(
            "group02",
            changed_command,
            idempotency_key="conflict-test",
            expected_version=None,
        )

    with sqlite3.connect(repo.database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

    assert count == 1


def test_concurrent_duplicate_create_only_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database = tmp_path / "briefing.db"
    SQLiteBriefingRepository(database)

    command = {
        "task_type": "briefing",
        "command_type": "create",
        "target_id": None,
        "parameters": {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    }

    barrier = Barrier(2)

    def worker():
        repo = SQLiteBriefingRepository(database)
        barrier.wait(timeout=10)
        return repo.execute_confirmed_action(
            "group02",
            command,
            idempotency_key="concurrent-test",
            expected_version=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]

    assert results[0] == results[1]
    assert results[0]["ok"] is True

    with sqlite3.connect(database) as connection:
        schedules = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

        receipts = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts"
        ).fetchone()[0]

    assert schedules == 1
    assert receipts == 1
