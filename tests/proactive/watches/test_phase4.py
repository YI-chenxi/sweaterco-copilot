"""P5 Phase 4: deterministic simulated observations and temporary SQLite only."""

import csv
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent.contracts import Evidence, ToolResult
from app.agent.proactive.watches.memory_repository import InMemoryWatchRepository
from app.agent.proactive.watches.models import WatchValidationError
from app.agent.proactive.watches.order_source import OrderLookupSource
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository
from app.tools.order_lookup import OrderLookupTool

NOW = datetime(2026, 4, 1, 8, tzinfo=timezone.utc)
STEP = timedelta(minutes=15)


def status_params(**updates):
    result = {
        "order_id": "ORD-002",
        "condition_type": "status_equals",
        "expected_status": "COMPLETE",
        "check_interval_minutes": 15,
    }
    result.update(updates)
    return result


def deadline_params(deadline=NOW + STEP * 2, **updates):
    result = {
        "order_id": "ORD-002",
        "condition_type": "no_progress_by_deadline",
        "deadline_at": deadline.isoformat(),
        "check_interval_minutes": 15,
    }
    result.update(updates)
    return result


def row(status="IN_PROGRESS", **updates):
    data = {
        "order_id": "ORD-002",
        "status": status,
        "last_activity_date": "2026-03-23",
        "current_stage": "WASHING",
    }
    data.update(updates)
    return ToolResult(data, [Evidence("orders.csv", "ORD-002", "Simulated snapshot")])


def source(value=None, *, sequence=None):
    mock = Mock()
    if sequence is not None:
        mock.fetch.side_effect = sequence
    else:
        mock.fetch.return_value = value if value is not None else row()
    return mock


def service(repo, order_source=None, *, scope=("a",), key="create", now=NOW, **kwargs):
    return WatchService(
        repo,
        order_source=order_source,
        action_id_provider=lambda: key,
        run_workspaces=scope,
        clock=lambda: now,
        **kwargs,
    )


def create(repo, parameters=None, *, workspace="a", key="create"):
    result = service(repo, key=key).create_watch(workspace, parameters or status_params())
    assert result["ok"], result
    return result["id"]


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    value = (
        InMemoryWatchRepository(clock=lambda: NOW)
        if request.param == "memory"
        else SQLiteWatchRepository(tmp_path / "watches.db", clock=lambda: NOW)
    )
    yield value
    if request.param == "sqlite":
        value.close()


def test_status_edges_unknown_does_not_reset_and_nonmatch_rearms(repo):
    watch_id = create(repo)
    src = source(
        sequence=[
            row("COMPLETE"),
            row("COMPLETE"),
            OSError("private"),
            row("COMPLETE"),
            row(),
            ToolResult({}),
            row("COMPLETE"),
        ]
    )
    svc = service(repo, src)
    outcomes = []
    for index, expected_alerts in enumerate([1, 1, 1, 1, 1, 1, 2]):
        results = svc.run_due_watches(NOW + index * STEP)
        assert len(results) == 1 and results[0]["ok"]
        assert set(results[0]) == {"ok", "status", "id", "data", "error_code", "message"}
        outcomes.append(results[0]["status"])
        assert len(repo.list_alerts("a")) == expected_alerts
        assert svc.run_due_watches(NOW + index * STEP) == []
    assert outcomes == [
        "matched",
        "matched",
        "unavailable",
        "matched",
        "not_matched",
        "unavailable",
        "matched",
    ]
    assert src.fetch.call_count == 7
    assert len(repo.list_checks("a")) == 7
    assert repo.get_watch_state("a", watch_id).version == "1"


def test_deadline_fixed_baseline_and_no_second_cycle(repo):
    deadline = NOW + STEP * 2
    watch_id = create(repo, deadline_params(deadline))
    original = row()
    src = source(sequence=[original, row(), row(last_activity_date="2026-03-24"), row()])
    svc = service(repo, src)
    assert svc.run_due_watches(NOW)[0]["status"] == "not_due"
    captured = repo.get_runtime("a", watch_id)
    assert captured.baseline == original and captured.baseline_at == NOW
    assert captured.next_check_at == deadline
    original.data["status"] = "COMPLETE"
    assert repo.get_runtime("a", watch_id).baseline.data["status"] == "IN_PROGRESS"
    assert svc.run_due_watches(deadline - timedelta(microseconds=1)) == []
    for index, outcome in enumerate(["matched", "not_matched", "matched"]):
        result = svc.run_due_watches(deadline + index * STEP)
        assert result[0]["status"] == outcome
        assert len(repo.list_alerts("a")) == 1
        assert repo.get_runtime("a", watch_id).baseline == captured.baseline
    assert repo.get_runtime("a", watch_id).alert_latched
    assert repo.get_watch_state("a", watch_id).version == "1"


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(microseconds=1), STEP * 5])
def test_no_baseline_at_or_after_deadline_is_always_unavailable(repo, offset):
    deadline = NOW + STEP
    watch_id = create(repo, deadline_params(deadline))
    svc = service(repo, source(row("COMPLETE")))
    result = svc.run_due_watches(deadline + offset)
    assert result[0]["status"] == "unavailable"
    assert repo.get_runtime("a", watch_id).baseline is None
    assert not repo.list_alerts("a")
    assert not repo.get_watch_state("a", watch_id).cancelled


def test_last_microsecond_before_deadline_can_capture_baseline(repo):
    deadline = NOW + STEP
    watch_id = create(repo, deadline_params(deadline))
    svc = service(repo, source())
    instant = deadline - timedelta(microseconds=1)
    assert svc.run_due_watches(instant)[0]["status"] == "not_due"
    assert repo.get_runtime("a", watch_id).baseline_at == instant
    result = svc.run_due_watches(deadline)
    assert result[0]["status"] == "matched"
    assert len(repo.list_alerts("a")) == 1


def test_failed_baseline_initialization_retries_without_moving_baseline(repo):
    deadline = NOW + STEP * 2 + timedelta(minutes=3)
    watch_id = create(repo, deadline_params(deadline))
    src = source(sequence=[OSError("private retrieval failure"), row(), row()])
    svc = service(repo, src)
    first = svc.run_due_watches(NOW)[0]
    assert first["status"] == "unavailable" and "private" not in str(first)
    assert repo.get_runtime("a", watch_id).next_check_at == NOW + STEP
    assert repo.get_runtime("a", watch_id).baseline is None
    assert svc.run_due_watches(NOW + STEP)[0]["status"] == "not_due"
    assert repo.get_runtime("a", watch_id).baseline_at == NOW + STEP
    assert repo.get_runtime("a", watch_id).next_check_at == deadline
    assert svc.run_due_watches(deadline)[0]["status"] == "matched"


def test_current_state_once_after_missed_status_checks_keeps_grid(repo):
    watch_id = create(repo)
    src = source(row("COMPLETE"))
    svc = service(repo, src)
    delayed = NOW + STEP * 10 + timedelta(minutes=2)
    result = svc.run_due_watches(delayed)
    assert len(result) == 1 and result[0]["status"] == "matched"
    assert repo.list_checks("a")[0].timestamp == delayed
    assert repo.get_runtime("a", watch_id).next_check_at == NOW + STEP * 11
    assert svc.run_due_watches(delayed - timedelta(minutes=1)) == []
    assert src.fetch.call_count == 1


def test_delayed_deadline_check_uses_current_timestamp_not_historical_deadline(repo):
    deadline = NOW + STEP
    watch_id = create(repo, deadline_params(deadline))
    svc = service(repo, source())
    svc.run_due_watches(NOW)
    delayed = deadline + STEP * 3 + timedelta(seconds=20)
    result = svc.run_due_watches(delayed)[0]
    assert result["status"] == "matched"
    assert repo.list_checks("a")[-1].timestamp == delayed
    assert repo.get_runtime("a", watch_id).next_check_at == deadline + STEP * 4


def test_source_can_initialize_immediately_after_creation_without_changing_receipt(repo):
    src = source()
    svc = service(repo, src)
    params = deadline_params()
    created = svc.create_watch("a", params)
    assert created["status"] == "created"
    assert repo.get_runtime("a", created["id"]).baseline_at == NOW
    assert len(repo.list_checks("a")) == 1
    assert svc.create_watch("a", params) == created
    assert src.fetch.call_count == 1


def test_immediate_status_check_and_failed_bootstrap_never_undo_creation(repo, monkeypatch):
    created = service(repo, source(row("COMPLETE"))).create_watch("a", status_params())
    assert created["ok"] and len(repo.list_alerts("a")) == 1
    with monkeypatch.context() as patch:
        patch.setattr(repo, "process_due", Mock(side_effect=OSError("private")))
        second = service(repo, source(), key="second").create_watch("a", status_params())
    assert second["ok"]
    assert repo.get_receipt("a", "second") == second
    assert repo.get_runtime("a", second["id"]).next_check_at == NOW
    assert len(service(repo, source()).run_due_watches(NOW)) == 1


def test_cancelled_watch_is_never_retrieved_or_checked(repo):
    watch_id = create(repo)
    src = source(row("COMPLETE"))
    assert service(repo).cancel_watch("a", watch_id)["ok"]
    assert service(repo, src).run_due_watches(NOW) == []
    assert repo.process_due("a", watch_id, NOW, Mock(side_effect=AssertionError)) is None
    src.fetch.assert_not_called()
    assert not repo.list_checks("a") and not repo.list_alerts("a")


def test_cancellation_after_listing_is_rechecked_before_retrieval(repo, monkeypatch):
    watch_id = create(repo)
    original = repo.process_due

    def cancel_first(*args):
        service(repo).cancel_watch("a", watch_id)
        return original(*args)

    monkeypatch.setattr(repo, "process_due", cancel_first)
    src = source()
    assert service(repo, src).run_due_watches(NOW) == []
    src.fetch.assert_not_called()


def test_runner_scope_and_runtime_are_workspace_isolated(repo):
    first = create(repo, workspace="a")
    second = create(repo, workspace="b")
    src = source(row("COMPLETE"))
    assert len(service(repo, src, scope=("a", "a")).run_due_watches(NOW)) == 1
    src.fetch.assert_called_once_with("a", "ORD-002")
    assert repo.get_runtime("b", first) is None
    assert repo.get_runtime("a", second) is None
    assert repo.list_checks("b") == [] and repo.list_alerts("b") == []
    assert repo.get_runtime("b", second).next_check_at == NOW
    assert repo.process_due("b", first, NOW, Mock(side_effect=AssertionError)) is None
    assert repo.list_due_ids("b", NOW) == [second]


@pytest.mark.parametrize("scope,src", [((), source()), (("a",), None)])
def test_due_execution_requires_explicit_source_and_scope(repo, scope, src):
    create(repo)
    with pytest.raises(WatchValidationError):
        service(repo, src, scope=scope).run_due_watches(NOW)
    assert repo.list_checks("a") == []


@pytest.mark.parametrize("bad_now", [None, "2026-04-01", NOW.replace(tzinfo=None)])
def test_due_interfaces_require_aware_utc(repo, bad_now):
    watch_id = create(repo)
    for call in [
        lambda: service(repo, source()).run_due_watches(bad_now),
        lambda: repo.list_due_ids("a", bad_now),
        lambda: repo.process_due("a", watch_id, bad_now, Mock()),
    ]:
        with pytest.raises(WatchValidationError):
            call()
    assert repo.list_checks("a") == []


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        ToolResult({}),
        row(order_id="ORD-999"),
        row(status="DELAYED"),
        ToolResult(row().data),
        ToolResult(row().data, row().evidence, ["uncertain"]),
        ToolResult(row().data, [Evidence("", "ORD-002", "bad")]),
        row(status=None),
    ],
)
def test_invalid_status_source_never_alerts(repo, snapshot):
    create(repo)
    src = Mock()
    src.fetch.return_value = snapshot
    result = service(repo, src).run_due_watches(NOW)[0]
    assert result["status"] == "unavailable"
    assert len(repo.list_checks("a")) == 1 and not repo.list_alerts("a")


@pytest.mark.parametrize(
    "snapshot",
    [
        row(last_activity_date=None),
        row(last_activity_date="not-a-date"),
        row(last_activity_date="2026-04-02"),
        row(current_stage={"bad": 1}),
    ],
)
def test_invalid_baseline_is_not_captured(repo, snapshot):
    watch_id = create(repo, deadline_params())
    svc = service(repo, source(snapshot))
    assert svc.run_due_watches(NOW)[0]["status"] == "unavailable"
    assert repo.get_runtime("a", watch_id).baseline is None
    assert not repo.list_alerts("a")


def test_missing_activity_is_allowed_for_status_only(repo):
    create(repo)
    assert (
        service(repo, source(row("COMPLETE", last_activity_date=None))).run_due_watches(
            NOW,
        )[0]["status"]
        == "matched"
    )


def test_one_bad_order_does_not_block_another(repo):
    first = create(repo)
    second = create(repo, status_params(order_id="ORD-003"), key="second")
    src = Mock()

    def fetch(_workspace, order_id):
        if order_id == "ORD-002":
            raise OSError("private file error")
        return row("COMPLETE", order_id=order_id)

    src.fetch.side_effect = fetch
    results = service(repo, src).run_due_watches(NOW)
    assert [result["status"] for result in results] == ["unavailable", "matched"]
    assert [item.watch_id for item in repo.list_alerts("a")] == [second]
    assert [item.watch_id for item in repo.list_checks("a")] == [first, second]


def test_one_storage_failure_does_not_block_another(repo, monkeypatch):
    first = create(repo)
    second = create(repo, key="second")
    original = repo.process_due

    def fail_first(workspace, watch_id, now, evaluate):
        if watch_id == first:
            raise sqlite3.OperationalError("private database error")
        return original(workspace, watch_id, now, evaluate)

    monkeypatch.setattr(repo, "process_due", fail_first)
    result = service(repo, source(row("COMPLETE"))).run_due_watches(NOW)
    assert result[0]["error_code"] == "STORAGE_ERROR" and "private" not in str(result)
    assert result[1]["status"] == "matched"
    assert [item.watch_id for item in repo.list_alerts("a")] == [second]
    assert repo.get_runtime("a", first).next_check_at == NOW


def test_order_lookup_adapter_uses_existing_tool_and_rejects_wrong_workspace():
    tool = Mock()
    expected = row()
    tool.execute.return_value = expected
    adapter = OrderLookupSource("a", tool)
    assert adapter.fetch("a", "ORD-002") is expected
    tool.execute.assert_called_once_with(order_id="ORD-002")
    with pytest.raises(ValueError):
        adapter.fetch("b", "ORD-002")
    assert tool.execute.call_count == 1


def write_orders(path):
    # Exactly the fields accepted by the existing A1 CSV parser.
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "order_id",
                "customer",
                "product",
                "pieces",
                "due_date",
                "status",
                "current_stage",
                "last_activity_date",
                "days_late",
                "completed_date",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "order_id": "ORD-002",
                "customer": "Demo",
                "product": "TOPS",
                "pieces": "100",
                "due_date": "2026-04-01",
                "status": "COMPLETE",
                "current_stage": "PACKING",
                "last_activity_date": "2026-03-23",
                "days_late": "0",
                "completed_date": "2026-03-23",
            }
        )


def test_real_local_csv_adapter_no_external_service(repo, tmp_path):
    path = tmp_path / "orders.csv"
    write_orders(path)
    create(repo)
    adapter = OrderLookupSource("a", OrderLookupTool(path))
    result = service(repo, adapter).run_due_watches(NOW)[0]
    assert result["status"] == "matched"
    assert repo.list_alerts("a")[0].evidence[0].source == "orders.csv"


def test_restart_preserves_baseline_latch_next_check_and_occurrence(tmp_path):
    path = tmp_path / "restart.db"
    deadline = NOW + STEP
    with SQLiteWatchRepository(path, clock=lambda: NOW) as repo:
        watch_id = create(repo, deadline_params(deadline))
        svc = service(repo, source())
        assert svc.run_due_watches(NOW)[0]["status"] == "not_due"
        before = repo.get_runtime("a", watch_id)
    with SQLiteWatchRepository(path) as repo:
        assert repo.get_runtime("a", watch_id) == before
        svc = service(repo, source())
        assert svc.run_due_watches(deadline)[0]["status"] == "matched"
        assert len(repo.list_checks("a")) == 2 and len(repo.list_alerts("a")) == 1
    with SQLiteWatchRepository(path) as repo:
        src = source()
        svc = service(repo, src)
        assert svc.run_due_watches(deadline) == []
        src.fetch.assert_not_called()
        assert svc.run_due_watches(deadline + STEP)[0]["status"] == "matched"
        assert len(repo.list_checks("a")) == 3 and len(repo.list_alerts("a")) == 1
        assert repo.get_runtime("a", watch_id).baseline == before.baseline
        assert repo._connection.execute("SELECT count(*) FROM p5_watch_runs").fetchone()[0] == 3
        assert repo._connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_status_rearming_survives_restarts(tmp_path):
    path = tmp_path / "status-restart.db"
    for index, status in enumerate(["COMPLETE", "COMPLETE", "IN_PROGRESS", "COMPLETE"]):
        with SQLiteWatchRepository(path, clock=lambda: NOW) as repo:
            if index == 0:
                create(repo)
            result = service(repo, source(row(status))).run_due_watches(NOW + index * STEP)
            assert len(result) == 1
            assert len(repo.list_alerts("a")) == (2 if index == 3 else 1)


@pytest.mark.parametrize("kind", ["status", "deadline"])
def test_concurrent_connections_process_one_occurrence_only(tmp_path, kind):
    path = tmp_path / "concurrent.db"
    with (
        SQLiteWatchRepository(path, clock=lambda: NOW) as one,
        SQLiteWatchRepository(path, clock=lambda: NOW) as two,
    ):
        if kind == "status":
            watch_id = create(one)
            instant = NOW
            expected_checks = 1
        else:
            watch_id = create(one, deadline_params(NOW + STEP))
            service(one, source()).run_due_watches(NOW)
            instant = NOW + STEP
            expected_checks = 2
        src = source(row("COMPLETE") if kind == "status" else row())
        start = Barrier(2)

        def run(repo):
            start.wait(timeout=10)
            return service(repo, src).run_due_watches(instant)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [one, two]))
        assert sorted(map(len, results)) == [0, 1]
        assert src.fetch.call_count == 1
        assert len(one.list_checks("a")) == expected_checks
        assert len(one.list_alerts("a")) == 1
        assert one.get_watch_state("a", watch_id).version == "1"


@pytest.mark.parametrize(
    "table,operation",
    [
        ("p5_checks", "INSERT"),
        ("p5_alerts", "INSERT"),
        ("p5_watch_runs", "INSERT"),
        ("p5_watch_runtime", "UPDATE"),
    ],
)
def test_sqlite_failure_rolls_back_entire_check_batch(tmp_path, table, operation):
    with SQLiteWatchRepository(tmp_path / "rollback.db", clock=lambda: NOW) as repo:
        watch_id = create(repo)
        before = repo.get_runtime("a", watch_id)
        # Test-owned table/operation allowlist, never user input.
        repo._connection.execute(
            f"CREATE TRIGGER fail_batch BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'private simulated failure'); END",
        )
        result = service(repo, source(row("COMPLETE"))).run_due_watches(NOW)[0]
        assert result["error_code"] == "STORAGE_ERROR" and "private" not in str(result)
        assert repo.get_runtime("a", watch_id) == before
        assert repo.list_checks("a") == [] and repo.list_alerts("a") == []
        assert repo._connection.execute("SELECT count(*) FROM p5_watch_runs").fetchone()[0] == 0
        assert repo._connection.execute("SELECT count(*) FROM p5_object_ids").fetchone()[0] == 1
        assert not repo._connection.in_transaction
        repo._connection.execute("DROP TRIGGER fail_batch")
        assert service(repo, source(row("COMPLETE"))).run_due_watches(NOW)[0]["status"] == "matched"
        assert len(repo.list_alerts("a")) == 1


def test_baseline_capture_rolls_back_with_failed_next_check_write(tmp_path):
    with SQLiteWatchRepository(tmp_path / "baseline-rollback.db", clock=lambda: NOW) as repo:
        watch_id = create(repo, deadline_params())
        repo._connection.execute(
            "CREATE TRIGGER fail_runtime BEFORE UPDATE ON p5_watch_runtime "
            "BEGIN SELECT RAISE(ABORT,'simulated'); END",
        )
        result = service(repo, source()).run_due_watches(NOW)[0]
        assert result["error_code"] == "STORAGE_ERROR"
        assert repo.get_runtime("a", watch_id).baseline is None
        assert repo.list_checks("a") == []
        repo._connection.execute("DROP TRIGGER fail_runtime")
        assert service(repo, source()).run_due_watches(NOW + STEP)[0]["status"] == "not_due"
        assert repo.get_runtime("a", watch_id).baseline_at == NOW + STEP


def test_committed_check_lost_acknowledgement_is_not_reprocessed(tmp_path, monkeypatch):
    path = tmp_path / "unknown.db"
    with SQLiteWatchRepository(path, clock=lambda: NOW) as repo:
        create(repo)
        original = repo.process_due

        def commit_then_fail(*args):
            original(*args)
            raise sqlite3.OperationalError("private acknowledgement loss")

        monkeypatch.setattr(repo, "process_due", commit_then_fail)
        result = service(repo, source(row("COMPLETE"))).run_due_watches(NOW)[0]
        assert result["error_code"] == "STORAGE_ERROR"
        assert len(repo.list_checks("a")) == 1 and len(repo.list_alerts("a")) == 1
        saved = repo._connection.execute("SELECT receipt FROM p5_watch_runs").fetchone()[0]
        receipt = json.loads(saved)
        assert receipt["status"] == "matched"
    with SQLiteWatchRepository(path) as repo:
        src = source(row("COMPLETE"))
        assert service(repo, src).run_due_watches(NOW) == []
        src.fetch.assert_not_called()
        assert len(repo.list_alerts("a")) == 1


def test_additive_phase3_backfill_uses_creation_time_and_never_invents_baseline(tmp_path):
    path = tmp_path / "legacy.db"
    with SQLiteWatchRepository(path, clock=lambda: NOW) as repo:
        watch_id = create(repo, deadline_params(NOW - STEP))
        repo._connection.execute("DROP TABLE p5_watch_runs")
        repo._connection.execute("DROP TABLE p5_watch_runtime")
    with SQLiteWatchRepository(path) as repo:
        runtime = repo.get_runtime("a", watch_id)
        assert runtime.anchor_at == NOW and runtime.next_check_at == NOW
        assert runtime.baseline is None
        result = service(repo, source()).run_due_watches(NOW)[0]
        assert result["status"] == "unavailable" and not repo.list_alerts("a")


def test_corrupt_one_watch_does_not_block_other_due_watch(tmp_path):
    with SQLiteWatchRepository(tmp_path / "corrupt.db", clock=lambda: NOW) as repo:
        first = create(repo)
        second = create(repo, key="second")
        repo._connection.execute("UPDATE p5_watches SET document='broken' WHERE id=?", (first,))
        results = service(repo, source(row("COMPLETE"))).run_due_watches(NOW)
        assert results[0]["error_code"] == "STORAGE_ERROR"
        assert results[1]["status"] == "matched"
        assert repo.list_checks("a")[0].watch_id == second


def test_record_identifier_collision_rolls_back_memory_and_sqlite(repo):
    create(repo)
    collision = str(uuid4())
    result = service(
        repo,
        source(row("COMPLETE")),
        record_id_factory=lambda: collision,
    ).run_due_watches(NOW)[0]
    assert result["error_code"] == "STORAGE_ERROR"
    assert repo.list_checks("a") == [] and repo.list_alerts("a") == []
    assert len(service(repo, source(row("COMPLETE"))).run_due_watches(NOW)) == 1


def test_runner_process_runs_once_exits_and_deduplicates_after_restart(tmp_path):
    database = tmp_path / "runner.db"
    orders = tmp_path / "orders.csv"
    write_orders(orders)
    with SQLiteWatchRepository(database, clock=lambda: NOW) as repo:
        create(repo, status_params(check_interval_minutes=1440 * 365))
    command = [
        sys.executable,
        "-B",
        "-m",
        "app.agent.proactive.watches.runner",
        "--database",
        str(database),
        "--workspace",
        "a",
        "--orders",
        str(orders),
    ]
    first = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
    second = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
    assert len(json.loads(first.stdout)) == 1 and json.loads(second.stdout) == []
    assert "not live" in first.stderr
    with SQLiteWatchRepository(database) as repo:
        assert len(repo.list_checks("a")) == 1 and len(repo.list_alerts("a")) == 1


def test_runner_unavailable_returns_nonzero_without_private_source_error(tmp_path):
    database = tmp_path / "unavailable.db"
    with SQLiteWatchRepository(database, clock=lambda: NOW) as repo:
        create(repo)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.agent.proactive.watches.runner",
            "--database",
            str(database),
            "--workspace",
            "a",
            "--orders",
            str(tmp_path / "private-nonexistent.csv"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)[0]["status"] == "unavailable"
    assert "private-nonexistent" not in result.stdout + result.stderr
    with SQLiteWatchRepository(database) as repo:
        assert len(repo.list_checks("a")) == 1 and not repo.list_alerts("a")


def test_shared_repository_concurrent_runs_retrieve_once(repo):
    create(repo)
    src = source(row("COMPLETE"))
    svc = service(repo, src)
    start = Barrier(4)

    def run(_index):
        start.wait(timeout=10)
        return svc.run_due_watches(NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(run, range(4)))
    assert sum(map(len, result)) == 1
    assert src.fetch.call_count == 1
    assert len(repo.list_checks("a")) == 1 and len(repo.list_alerts("a")) == 1


def test_cancellation_waits_for_atomic_check_then_preserves_history(tmp_path):
    from threading import Event

    path = tmp_path / "cancel-race.db"
    with (
        SQLiteWatchRepository(path, clock=lambda: NOW) as one,
        SQLiteWatchRepository(path, clock=lambda: NOW) as two,
    ):
        watch_id = create(one)
        in_source, cancellation_started = Event(), Event()
        src = Mock()

        def fetch(_workspace, _order_id):
            in_source.set()
            assert cancellation_started.wait(10)
            return row("COMPLETE")

        def cancel():
            assert in_source.wait(10)
            cancellation_started.set()
            return service(two).cancel_watch("a", watch_id)

        src.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            checking = pool.submit(service(one, src).run_due_watches, NOW)
            cancelling = pool.submit(cancel)
            assert checking.result(timeout=15)[0]["status"] == "matched"
            assert cancelling.result(timeout=15)["ok"]
        assert one.get_watch_state("a", watch_id).cancelled
        assert one.get_watch_state("a", watch_id).version == "2"
        assert len(one.list_checks("a")) == 1 and len(one.list_alerts("a")) == 1
        assert service(one, src).run_due_watches(NOW + STEP) == []
        assert src.fetch.call_count == 1


def test_runtime_reads_are_detached(repo):
    watch_id = create(repo, deadline_params())
    service(repo, source()).run_due_watches(NOW)
    original = repo.get_runtime("a", watch_id)
    changed = deepcopy(original)
    changed.baseline.data["status"] = "COMPLETE"
    changed.baseline.evidence.clear()
    assert repo.get_runtime("a", watch_id) == original


def test_runner_failure_to_list_one_workspace_continues_other_scope(repo, monkeypatch):
    create(repo, workspace="b")
    original = repo.list_due_ids

    def list_due(workspace, now):
        if workspace == "a":
            raise sqlite3.OperationalError("private")
        return original(workspace, now)

    monkeypatch.setattr(repo, "list_due_ids", list_due)
    result = service(repo, source(row("COMPLETE")), scope=("a", "b")).run_due_watches(NOW)
    assert result[0]["error_code"] == "STORAGE_ERROR"
    assert result[1]["status"] == "matched"
    assert not repo.list_alerts("a") and len(repo.list_alerts("b")) == 1


def test_completed_deadline_order_does_not_emit_inactivity_alert(repo):
    watch_id = create(repo, deadline_params(NOW + STEP))
    svc = service(repo, source(sequence=[row(), row("COMPLETE")]))
    assert svc.run_due_watches(NOW)[0]["status"] == "not_due"
    assert svc.run_due_watches(NOW + STEP)[0]["status"] == "not_matched"
    assert not repo.list_alerts("a")
    assert not repo.get_watch_state("a", watch_id).cancelled


def test_abrupt_writer_process_exit_rolls_back_check_and_runtime(tmp_path):
    path = tmp_path / "crash.db"
    with SQLiteWatchRepository(path, clock=lambda: NOW) as repo:
        watch_id = create(repo)
        before = repo.get_runtime("a", watch_id)
    script = """
import os,sys
from datetime import datetime
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository
from app.agent.proactive.watches.service import WatchService
from app.agent.contracts import Evidence,ToolResult
class Source:
    def fetch(self,workspace_id,order_id):
        return ToolResult({'order_id':order_id,'status':'COMPLETE'},[Evidence('test','id','mock')])
repo=SQLiteWatchRepository(sys.argv[1])
original=repo._insert_record
def crash(record,*,alert):
    original(record,alert=alert)
    if alert:
        os._exit(23)
repo._insert_record=crash
WatchService(repo,order_source=Source(),run_workspaces=('a',)).run_due_watches(
    datetime.fromisoformat(sys.argv[2]))
"""
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(path),
            NOW.isoformat(),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 23
    with SQLiteWatchRepository(path) as repo:
        assert repo.get_runtime("a", watch_id) == before
        assert repo.list_checks("a") == [] and repo.list_alerts("a") == []
        assert repo._connection.execute("SELECT count(*) FROM p5_watch_runs").fetchone()[0] == 0
        results = service(repo, source(row("COMPLETE"))).run_due_watches(NOW)
        assert results[0]["status"] == "matched"
        assert len(repo.list_alerts("a")) == 1


def test_reentrant_memory_source_cancellation_prevents_a_late_check():
    repo = InMemoryWatchRepository(clock=lambda: NOW)
    watch_id = create(repo)
    src = Mock()

    def fetch(_workspace, _order_id):
        service(repo).cancel_watch("a", watch_id)
        return row("COMPLETE")

    src.fetch.side_effect = fetch
    assert service(repo, src).run_due_watches(NOW) == []
    assert repo.list_checks("a") == [] and repo.list_alerts("a") == []


def test_read_finishing_at_deadline_cannot_backdate_a_baseline(repo):
    watch_id = create(repo, deadline_params(NOW + STEP))
    src = source()
    clock = [NOW]

    def fetch(_workspace, _order_id):
        clock[0] = NOW + STEP
        return row()

    src.fetch.side_effect = fetch
    svc = WatchService(
        repo,
        order_source=src,
        run_workspaces=("a",),
        clock=lambda: clock[0],
    )
    result = svc.run_due_watches(NOW)[0]
    assert result["status"] == "unavailable"
    assert repo.get_runtime("a", watch_id).baseline is None
    assert repo.list_checks("a")[0].timestamp == NOW + STEP
    assert not repo.list_alerts("a")
