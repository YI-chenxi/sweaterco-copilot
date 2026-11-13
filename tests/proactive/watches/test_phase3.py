"""Persistent repository/recovery tests; temporary databases, no P4 or API calls."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent.contracts import Evidence
from app.agent.proactive.watches.memory_repository import InMemoryWatchRepository
from app.agent.proactive.watches.models import (
    Alert,
    CheckOutcome,
    CheckRecord,
    WatchValidationError,
)
from app.agent.proactive.watches.repository import (
    CancellationContext,
    RepositoryConflict,
    RepositoryError,
)
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository

NOW = datetime(2026, 4, 1, 8, tzinfo=timezone.utc)


def params(**updates):
    result = {
        "order_id": "ORD-002",
        "condition_type": "no_progress_by_deadline",
        "deadline_at": NOW.isoformat(),
        "check_interval_minutes": 15,
    }
    result.update(updates)
    return result


def service(repo, key="create-1", context=None):
    return WatchService(
        repo,
        action_id_provider=lambda: key,
        cancellation_context_provider=(lambda: context) if context is not None else None,
    )


def record(watch_id, *, alert=False, workspace="a"):
    return (Alert if alert else CheckRecord)(
        id=str(uuid4()),
        workspace_id=workspace,
        watch_id=watch_id,
        timestamp=NOW,
        observed_value={"status": "IN_PROGRESS"},
        outcome=CheckOutcome.MATCHED,
        evidence=(Evidence("orders.csv", "ORD-002", "Controlled test snapshot"),),
    )


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    value = (
        InMemoryWatchRepository()
        if request.param == "memory"
        else SQLiteWatchRepository(tmp_path / "watches.db", clock=lambda: NOW)
    )
    yield value
    if request.param == "sqlite":
        value.close()


def test_repository_contract_replay_and_versions(repo):
    svc = service(repo)
    created = svc.create_watch("a", params())
    assert created["ok"]
    watch_id = created["id"]
    initial = repo.get_watch_state("a", watch_id)
    assert initial.version == "1" and not initial.cancelled
    check, alert = record(watch_id), record(watch_id, alert=True)
    repo.save_check(check)
    repo.save_alert(alert)
    assert repo.get_watch_state("a", watch_id) == initial
    conditional = service(repo, context=CancellationContext("cancel-1", "1"))
    cancelled = conditional.cancel_watch("a", watch_id)
    assert cancelled["ok"]
    assert repo.get_watch_state("a", watch_id).version == "2"
    assert repo.get_watch_state("a", watch_id).cancelled
    assert conditional.cancel_watch("a", watch_id) == cancelled
    assert repo.get_watch_state("a", watch_id).version == "2"
    assert svc.create_watch("a", params()) == created
    assert svc.list_watches("a") == []
    assert repo.list_checks("a") == [check]
    assert repo.list_alerts("a") == [alert]
    assert repo.get_receipt("a", "create-1") == created
    assert repo.get_receipt("a", "cancel-1") == cancelled


def test_conditional_conflicts_are_no_ops(repo):
    created = service(repo).create_watch("a", params())
    watch_id = created["id"]
    state = repo.get_watch_state("a", watch_id)
    wrong = service(repo, context=CancellationContext("cancel-1", "2"))
    assert wrong.cancel_watch("a", watch_id)["error_code"] == "CONFLICT"
    assert repo.get_watch_state("a", watch_id) == state
    assert repo.get_receipt("a", "cancel-1") is None
    assert service(repo).cancel_watch("a", watch_id)["ok"]
    strict = service(repo, context=CancellationContext("new-cancel", "1"))
    assert strict.cancel_watch("a", watch_id)["error_code"] == "CONFLICT"


def test_execution_key_cannot_change_target_operation_or_parameters(repo):
    first = service(repo, "one").create_watch("a", params())
    second = service(repo, "two").create_watch("a", params())
    assert (
        service(repo, "one").create_watch("a", params(check_interval_minutes=30))["error_code"]
        == "CONFLICT"
    )
    assert (
        service(repo, context=CancellationContext("one", "1")).cancel_watch(
            "a",
            first["id"],
        )["error_code"]
        == "CONFLICT"
    )
    cancelled = service(repo, context=CancellationContext("cancel", "1"))
    assert cancelled.cancel_watch("a", first["id"])["ok"]
    assert cancelled.cancel_watch("a", second["id"])["error_code"] == "CONFLICT"
    assert (
        service(repo, context=CancellationContext("cancel", "2")).cancel_watch(
            "a",
            first["id"],
        )["error_code"]
        == "CONFLICT"
    )
    assert not repo.get_watch_state("a", second["id"]).cancelled


def test_workspace_scoped_state_receipts_and_record_links(repo):
    first = service(repo).create_watch("a", params())
    other = service(repo).create_watch("b", params())
    assert first["id"] != other["id"]
    assert repo.get_watch_state("b", first["id"]) is None
    assert repo.get_receipt("empty", "create-1") is None
    assert repo.get_receipt("b", "create-1")["id"] == other["id"]
    for cancel in [service(repo), service(repo, context=CancellationContext("cancel", "1"))]:
        assert cancel.cancel_watch("b", first["id"])["error_code"] == "NOT_FOUND"
    assert repo.get_receipt("b", "cancel") is None
    for is_alert in [False, True]:
        item = record(first["id"], alert=is_alert)
        save = repo.save_alert if is_alert else repo.save_check
        with pytest.raises(WatchValidationError):
            save(replace(item, workspace_id="b"))
        save(item)
        save(item)
        with pytest.raises(RepositoryConflict):
            save(replace(item, observed_value={"status": "COMPLETE"}))
    assert repo.list_alerts("b") == [] and repo.list_checks("b") == []


def test_returned_receipts_are_detached(repo):
    created = service(repo).create_watch("a", params())
    retrieved = repo.get_receipt("a", "create-1")
    retrieved["data"]["order_id"] = "CHANGED"
    assert repo.get_receipt("a", "create-1") == created


@pytest.mark.parametrize("bad", [None, "", "0", "01", "-1", "1.0", 1, True, "９"])
def test_invalid_versions_cannot_reach_writes(repo, bad):
    with pytest.raises(WatchValidationError):
        CancellationContext("key", bad)
    with pytest.raises(WatchValidationError):
        repo.cancel_watch_if_version("a", str(uuid4()), bad, "key")


@pytest.mark.parametrize("workspace", [None, "", " a", 5])
def test_internal_interfaces_validate_workspace(repo, workspace):
    for call in [
        lambda: repo.get_receipt(workspace, "key"),
        lambda: repo.get_watch_state(workspace, str(uuid4())),
        lambda: repo.cancel_watch_if_version(workspace, str(uuid4()), "1", "key"),
    ]:
        with pytest.raises(WatchValidationError):
            call()


def test_context_provider_does_not_silently_fall_back(repo):
    created = service(repo).create_watch("a", params())
    missing = WatchService(repo, cancellation_context_provider=lambda: None)
    assert missing.cancel_watch("a", created["id"])["error_code"] == "INVALID_ARGUMENT"
    failure = WatchService(
        repo,
        cancellation_context_provider=Mock(side_effect=RuntimeError("private")),
    )
    result = failure.cancel_watch("a", created["id"])
    assert result["error_code"] == "EXECUTION_ERROR" and "private" not in str(result)
    assert not repo.get_watch_state("a", created["id"]).cancelled


def test_restart_preserves_all_records_tombstones_and_original_receipts(tmp_path):
    path = tmp_path / "watches.db"
    with SQLiteWatchRepository(path) as repo:
        created = service(repo).create_watch("a", params())
        check = record(created["id"])
        alert = record(created["id"], alert=True)
        repo.save_check(check)
        repo.save_alert(alert)
        cancelled = service(repo, context=CancellationContext("cancel", "1")).cancel_watch(
            "a",
            created["id"],
        )
    with SQLiteWatchRepository(path) as reopened:
        assert reopened.list_checks("a") == [check]
        assert reopened.list_alerts("a") == [alert]
        assert reopened.get_watch_state("a", created["id"]).cancelled
        assert service(reopened).create_watch("a", params()) == created
        assert (
            service(reopened, context=CancellationContext("cancel", "1")).cancel_watch(
                "a",
                created["id"],
            )
            == cancelled
        )
        assert reopened.list_watches("a") == []
        with pytest.raises(RepositoryConflict):
            reopened.save_alert(record(created["id"], alert=True))


def test_separate_process_recovers_receipts_and_records(tmp_path):
    path = tmp_path / "process.db"
    script = """
import json,sys
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.repository import CancellationContext
from app.agent.proactive.watches.models import CheckRecord, Alert, CheckOutcome
from app.agent.contracts import Evidence
from datetime import datetime,timezone
from uuid import uuid4
with SQLiteWatchRepository(sys.argv[1]) as repo:
    svc=WatchService(repo,action_id_provider=lambda:'create-process',
        cancellation_context_provider=lambda:CancellationContext('cancel-process','1'))
    created=svc.create_watch('a', json.loads(sys.argv[2]))
    for record_type, save in [(CheckRecord,repo.save_check),(Alert,repo.save_alert)]:
        save(record_type(id=str(uuid4()), workspace_id='a', watch_id=created['id'],
            timestamp=datetime(2026,4,1,tzinfo=timezone.utc), observed_value={'status':'COMPLETE'},
            evidence=(Evidence('orders.csv','ORD-002','Test'),), outcome=CheckOutcome.MATCHED))
    cancelled=svc.cancel_watch('a',created['id'])
    print(json.dumps([created,cancelled]))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), json.dumps(params())],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    created, cancelled = json.loads(result.stdout)
    with SQLiteWatchRepository(path) as repo:
        assert repo.get_receipt("a", "create-process") == created
        assert repo.get_receipt("a", "cancel-process") == cancelled
        assert repo.get_watch_state("a", created["id"]).version == "2"
        assert len(repo.list_checks("a")) == 1
        assert len(repo.list_alerts("a")) == 1


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_connections_creation_is_atomic(tmp_path, same_key):
    path = tmp_path / "concurrent.db"
    with SQLiteWatchRepository(path) as one, SQLiteWatchRepository(path) as two:
        services = [service(one, "key"), service(two, "key" if same_key else "different")]
        start = Barrier(2)

        def create(svc):
            start.wait(timeout=10)
            return svc.create_watch("a", params())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, services))
        assert all(r["ok"] for r in results)
        assert len(one.list_watches("a")) == (1 if same_key else 2)
        if same_key:
            assert results[0] == results[1]


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_conditional_cancellation(tmp_path, same_key):
    path = tmp_path / "cancel.db"
    with SQLiteWatchRepository(path) as one, SQLiteWatchRepository(path) as two:
        created = service(one).create_watch("a", params())
        services = [
            service(one, context=CancellationContext("key", "1")),
            service(two, context=CancellationContext("key" if same_key else "different", "1")),
        ]
        start = Barrier(2)

        def cancel(svc):
            start.wait(timeout=10)
            return svc.cancel_watch("a", created["id"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(cancel, services))
        assert sum(r["ok"] for r in results) == (2 if same_key else 1)
        if same_key:
            assert results[0] == results[1]
        else:
            assert {r["error_code"] for r in results} == {None, "CONFLICT"}
        assert one.get_watch_state("a", created["id"]).version == "2"


@pytest.mark.parametrize("operation", ["create", "cancel"])
def test_receipt_failure_rolls_back_business_state_and_receipt(tmp_path, monkeypatch, operation):
    with SQLiteWatchRepository(tmp_path / "rollback.db") as repo:
        created = service(repo).create_watch("a", params())
        before = repo.get_watch_state("a", created["id"])
        original = repo._save_receipt

        def fail_after_receipt(*args):
            original(*args)
            raise sqlite3.OperationalError("private simulated storage failure")

        with monkeypatch.context() as patch:
            patch.setattr(repo, "_save_receipt", fail_after_receipt)
            result = (
                service(repo, "retry").create_watch("a", params())
                if operation == "create"
                else service(repo, context=CancellationContext("retry", "1")).cancel_watch(
                    "a",
                    created["id"],
                )
            )
        assert result["error_code"] == "STORAGE_ERROR" and "private" not in str(result)
        assert repo.get_watch_state("a", created["id"]) == before
        assert len(repo.list_watches("a")) == 1
        assert repo.get_receipt("a", "retry") is None
        assert repo._connection.execute("SELECT count(*) FROM p5_object_ids").fetchone()[0] == 1
        assert not repo._connection.in_transaction


def test_write_succeeds_but_acknowledgement_is_lost_then_replay_recovers(tmp_path, monkeypatch):
    path = tmp_path / "unknown.db"
    with SQLiteWatchRepository(path) as repo:
        real = repo.create_watch

        def commit_then_fail(*args):
            real(*args)
            raise RepositoryError("unknown acknowledgement")

        with monkeypatch.context() as patch:
            patch.setattr(repo, "create_watch", commit_then_fail)
            assert service(repo).create_watch("a", params())["error_code"] == "STORAGE_ERROR"
        receipt = repo.get_receipt("a", "create-1")
        assert receipt["ok"] and len(repo.list_watches("a")) == 1
    with SQLiteWatchRepository(path) as repo:
        assert service(repo).create_watch("a", params()) == receipt
        assert len(repo.list_watches("a")) == 1


def test_record_insert_failure_rolls_back_uuid_registration(tmp_path):
    with SQLiteWatchRepository(tmp_path / "records.db") as repo:
        watch_id = service(repo).create_watch("a", params())["id"]
        item = record(watch_id)
        repo._connection.execute(
            "CREATE TRIGGER fail_check BEFORE INSERT ON p5_checks "
            "BEGIN SELECT RAISE(ABORT,'simulated'); END",
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.save_check(item)
        assert repo.list_checks("a") == []
        assert (
            repo._connection.execute(
                "SELECT 1 FROM p5_object_ids WHERE id=?",
                (item.id,),
            ).fetchone()
            is None
        )


def test_schema_foreign_keys_settings_and_p4_table_separation(tmp_path):
    path = tmp_path / "shared.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE action_proposals(dummy TEXT)")
        connection.execute("INSERT INTO action_proposals VALUES('keep')")
        connection.execute("CREATE TABLE action_audit(dummy TEXT)")
    with SQLiteWatchRepository(path) as repo:
        assert repo._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert repo._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert repo._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert repo._connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert repo._connection.execute("SELECT * FROM action_proposals").fetchall() == [("keep",)]
        with pytest.raises(sqlite3.IntegrityError):
            repo._connection.execute(
                "INSERT INTO p5_checks VALUES(?,?,?,?,?)",
                (str(uuid4()), "b", str(uuid4()), "x", "{}"),
            )
        assert repo._connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_parameterized_workspace_and_closed_storage_errors(tmp_path):
    repo = SQLiteWatchRepository(tmp_path / "safe.db")
    workspace = "a'; DROP TABLE p5_watches; --"
    created = service(repo).create_watch(workspace, params())
    assert created["ok"] and service(repo).list_watches(workspace) == [created["data"]]
    assert service(repo).list_watches("a") == []
    repo.close()
    assert service(repo).create_watch("a", params())["error_code"] == "STORAGE_ERROR"
    assert service(repo).cancel_watch(workspace, created["id"])["error_code"] == "STORAGE_ERROR"
    with pytest.raises(RepositoryError, match="Unable to list watches"):
        service(repo).list_watches(workspace)


def test_global_record_uuid_collision_and_normalized_lookup(tmp_path):
    with SQLiteWatchRepository(tmp_path / "uuid.db") as repo:
        created = service(repo).create_watch("a", params())
        watch_id = created["id"]
        with pytest.raises(RepositoryConflict):
            repo.save_check(replace(record(watch_id), id=watch_id))
        assert repo.get_watch_state("a", watch_id.upper().replace("-", "")).version == "1"


def test_repository_requires_utc_clock_for_new_writes(tmp_path):
    def naive_clock():
        return NOW.replace(tzinfo=None)

    with SQLiteWatchRepository(tmp_path / "clock.db", clock=naive_clock) as repo:
        assert not service(repo).create_watch("a", params())["ok"]
        assert repo.list_watches("a") == []


def test_corrupted_stored_document_is_not_an_empty_success(tmp_path):
    with SQLiteWatchRepository(tmp_path / "corrupt.db") as repo:
        created = service(repo).create_watch("a", params())
        repo._connection.execute(
            "UPDATE p5_watches SET document=? WHERE id=?",
            ("broken", created["id"]),
        )
        with pytest.raises(RepositoryError):
            service(repo).list_watches("a")


@pytest.mark.parametrize("operation", ["create", "cancel"])
def test_receipt_read_failure_after_commit_is_recoverable(tmp_path, monkeypatch, operation):
    path = tmp_path / "read-failure.db"
    with SQLiteWatchRepository(path) as repo:
        created = service(repo).create_watch("a", params())
        svc = service(repo, "second", CancellationContext("cancel", "1"))
        with monkeypatch.context() as patch:
            patch.setattr(
                repo,
                "get_receipt",
                Mock(side_effect=sqlite3.OperationalError("private")),
            )
            result = (
                svc.create_watch("a", params())
                if operation == "create"
                else svc.cancel_watch("a", created["id"])
            )
        assert result["error_code"] == "STORAGE_ERROR" and "private" not in str(result)
    with SQLiteWatchRepository(path) as repo:
        svc = service(repo, "second", CancellationContext("cancel", "1"))
        result = (
            svc.create_watch("a", params())
            if operation == "create"
            else svc.cancel_watch("a", created["id"])
        )
        assert result == repo.get_receipt("a", "second" if operation == "create" else "cancel")
        assert len(repo.list_watches("a")) == (2 if operation == "create" else 0)
        assert repo.get_watch_state("a", created["id"]).version == (
            "1" if operation == "create" else "2"
        )


def test_legacy_cancellation_receipt_replays_after_restart(tmp_path):
    path = tmp_path / "legacy.db"
    with SQLiteWatchRepository(path) as repo:
        created = service(repo).create_watch("a", params())
        original = service(repo).cancel_watch("a", created["id"])
    with SQLiteWatchRepository(path) as repo:
        assert service(repo).cancel_watch("a", created["id"]) == original
        assert repo.get_watch_state("a", created["id"]).version == "2"


def test_concurrent_same_key_different_parameters_only_one_commits(tmp_path):
    with (
        SQLiteWatchRepository(tmp_path / "different.db") as one,
        SQLiteWatchRepository(tmp_path / "different.db") as two,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        start = Barrier(2)

        def create(pair):
            repo, interval = pair
            start.wait(timeout=10)
            return service(repo).create_watch("a", params(check_interval_minutes=interval))

        results = list(pool.map(create, [(one, 15), (two, 30)]))
        assert sum(result["ok"] for result in results) == 1
        assert {result["error_code"] for result in results} == {None, "CONFLICT"}
        assert len(one.list_watches("a")) == 1
        successful = next(result for result in results if result["ok"])
        assert one.get_receipt("a", "create-1") == successful


def test_two_services_share_request_local_cancellation_context(tmp_path):
    from contextvars import ContextVar

    context = ContextVar("p5_mock_cancellation", default=None)
    with SQLiteWatchRepository(tmp_path / "context.db") as repo:
        first = service(repo, "first").create_watch("a", params())
        second = service(repo, "second").create_watch("a", params())
        svc = WatchService(repo, cancellation_context_provider=context.get)
        start = Barrier(2)

        def cancel(pair):
            watch, key = pair
            token = context.set(CancellationContext(key, "1"))
            try:
                start.wait(timeout=10)
                return svc.cancel_watch("a", watch["id"])
            finally:
                context.reset(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(cancel, [(first, "cancel-first"), (second, "cancel-second")]))
        assert all(result["ok"] for result in results)
        assert repo.get_receipt("a", "cancel-first")["id"] == first["id"]
        assert repo.get_receipt("a", "cancel-second")["id"] == second["id"]
        assert context.get() is None


def test_sqlite_busy_failure_does_not_write_or_return_empty_success(tmp_path):
    path = tmp_path / "busy.db"
    with SQLiteWatchRepository(path) as one, SQLiteWatchRepository(path) as two:
        two._connection.execute("PRAGMA busy_timeout=1")
        one._connection.execute("BEGIN IMMEDIATE")
        try:
            assert service(two).create_watch("a", params())["error_code"] == "STORAGE_ERROR"
            assert not two._connection.in_transaction
        finally:
            one._connection.execute("ROLLBACK")
        assert two.list_watches("a") == []
        assert two.get_receipt("a", "create-1") is None
        assert service(two).create_watch("a", params())["ok"]


def test_fingerprint_parameter_conflict_survives_restart(tmp_path):
    path = tmp_path / "fingerprint.db"
    with SQLiteWatchRepository(path) as repo:
        first = service(repo).create_watch("a", params())
    with SQLiteWatchRepository(path) as repo:
        assert (
            service(repo).create_watch("a", params(check_interval_minutes=16))["error_code"]
            == "CONFLICT"
        )
        assert service(repo).create_watch("a", params(order_id=" ord-002 ")) == first
        assert len(repo.list_watches("a")) == 1
