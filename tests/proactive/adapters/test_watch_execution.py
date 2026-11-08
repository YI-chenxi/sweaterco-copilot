"""Integration tests for P4 watch adapter and P5 SQLite storage."""

import sqlite3

from app.agent.proactive.adapters.watch_actions import WatchActionExecutor
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository


def create_command():
    return {
        "task_type": "watch",
        "command_type": "create",
        "target_id": None,
        "parameters": {
            "order_id": "ORD-057",
            "condition_type": "status_equals",
            "expected_status": "COMPLETE",
            "check_interval_minutes": 30,
        },
    }


def test_watch_create_is_durable_and_idempotent(tmp_path):
    database = tmp_path / "watches.db"
    repo = SQLiteWatchRepository(database)
    adapter = WatchActionExecutor(WatchService(repo))

    command = create_command()

    first = adapter.execute(
        "group02",
        command,
        idempotency_key="watch-create-001",
        expected_version=None,
    )

    second = adapter.execute(
        "group02",
        command,
        idempotency_key="watch-create-001",
        expected_version=None,
    )

    assert first["ok"] is True
    assert first == second

    with sqlite3.connect(database) as connection:
        watch_count = connection.execute(
            "SELECT COUNT(*) FROM p5_watches WHERE workspace_id = ?",
            ("group02",),
        ).fetchone()[0]

        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM p5_execution_receipts WHERE workspace_id = ?",
            ("group02",),
        ).fetchone()[0]

    assert watch_count == 1
    assert receipt_count == 1

    repo.close()


def test_watch_cancel_is_versioned_and_idempotent(tmp_path):
    database = tmp_path / "watches.db"
    repo = SQLiteWatchRepository(database)
    adapter = WatchActionExecutor(WatchService(repo))

    created = adapter.execute(
        "group02",
        create_command(),
        idempotency_key="watch-create-for-cancel",
        expected_version=None,
    )

    assert created["ok"] is True
    watch_id = created["id"]

    state = repo.get_watch_state("group02", watch_id)
    assert state is not None
    assert state.version == "1"
    assert state.cancelled is False

    command = {
        "task_type": "watch",
        "command_type": "cancel",
        "target_id": watch_id,
        "parameters": {},
    }

    assert adapter.validate("group02", command) == "1"

    first = adapter.execute(
        "group02",
        command,
        idempotency_key="watch-cancel-001",
        expected_version="1",
    )

    second = adapter.execute(
        "group02",
        command,
        idempotency_key="watch-cancel-001",
        expected_version="1",
    )

    assert first["ok"] is True
    assert first["status"] == "cancelled"
    assert first == second

    final = repo.get_watch_state("group02", watch_id)

    assert final is not None
    assert final.cancelled is True
    assert final.version == "2"

    with sqlite3.connect(database) as connection:
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM p5_execution_receipts "
            "WHERE workspace_id = ?",
            ("group02",),
        ).fetchone()[0]

    assert receipt_count == 2

    repo.close()


def test_stale_watch_cancel_is_rejected(tmp_path):
    database = tmp_path / "watches.db"
    repo = SQLiteWatchRepository(database)
    adapter = WatchActionExecutor(WatchService(repo))

    created = adapter.execute(
        "group02",
        create_command(),
        idempotency_key="stale-create",
        expected_version=None,
    )
    watch_id = created["id"]

    command = {
        "task_type": "watch",
        "command_type": "cancel",
        "target_id": watch_id,
        "parameters": {},
    }

    first = adapter.execute(
        "group02",
        command,
        idempotency_key="stale-cancel-first",
        expected_version="1",
    )
    assert first["ok"] is True

    second = adapter.execute(
        "group02",
        command,
        idempotency_key="stale-cancel-second",
        expected_version="1",
    )

    assert second["ok"] is False
    assert second["error_code"] == "CONFLICT"

    state = repo.get_watch_state("group02", watch_id)
    assert state.cancelled is True
    assert state.version == "2"

    repo.close()


def test_cross_workspace_watch_cancel_is_rejected(tmp_path):
    database = tmp_path / "watches.db"
    repo = SQLiteWatchRepository(database)
    adapter = WatchActionExecutor(WatchService(repo))

    created = adapter.execute(
        "group02",
        create_command(),
        idempotency_key="workspace-create",
        expected_version=None,
    )
    watch_id = created["id"]

    command = {
        "task_type": "watch",
        "command_type": "cancel",
        "target_id": watch_id,
        "parameters": {},
    }

    result = adapter.execute(
        "another-workspace",
        command,
        idempotency_key="workspace-cancel",
        expected_version="1",
    )

    assert result["ok"] is False
    assert result["error_code"] == "NOT_FOUND"

    original = repo.get_watch_state("group02", watch_id)
    assert original.cancelled is False
    assert original.version == "1"

    repo.close()


def test_same_key_different_create_parameters_conflict(tmp_path):
    repo = SQLiteWatchRepository(tmp_path / "watches.db")
    adapter = WatchActionExecutor(WatchService(repo))

    first = adapter.execute(
        "group02",
        create_command(),
        idempotency_key="watch-conflict-key",
        expected_version=None,
    )
    assert first["ok"] is True

    changed = create_command()
    changed["parameters"]["check_interval_minutes"] = 60

    second = adapter.execute(
        "group02",
        changed,
        idempotency_key="watch-conflict-key",
        expected_version=None,
    )

    assert second["ok"] is False
    assert second["error_code"] == "CONFLICT"
    assert len(repo.list_watches("group02")) == 1
    repo.close()


def test_storage_error_is_reported_as_uncertain():
    import pytest

    from app.agent.proactive.actions.models import ActionError

    class BrokenRepository:
        pass

    adapter = WatchActionExecutor(WatchService(BrokenRepository()))

    with pytest.raises(ActionError) as exc:
        adapter.execute(
            "group02",
            create_command(),
            idempotency_key="storage-error-test",
            expected_version=None,
        )

    assert exc.value.code == "STORAGE_ERROR"
