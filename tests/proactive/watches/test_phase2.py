"""Mock-driven management tests; no keys, files, business tools or external APIs."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent.contracts import Evidence
from app.agent.proactive.watches.memory_repository import InMemoryWatchRepository
from app.agent.proactive.watches.models import (
    Alert,
    CheckOutcome,
    CheckRecord,
    Watch,
    WatchValidationError,
)
from app.agent.proactive.watches.repository import RepositoryConflict, RepositoryError
from app.agent.proactive.watches.service import WatchService

NOW = datetime(2026, 4, 1, 8, tzinfo=timezone.utc)


def parameters(**changes):
    result = {
        "order_id": "ORD-002",
        "condition_type": "no_progress_by_deadline",
        "check_interval_minutes": 15,
        "deadline_at": NOW.isoformat(),
    }
    result.update(changes)
    return result


@pytest.fixture
def setup():
    repo = InMemoryWatchRepository()
    action = Mock(return_value="confirmed-action-1")
    return repo, action, WatchService(repo, action_id_provider=action)


def test_create_and_replay_return_same_contract_result(setup):
    repo, action, service = setup
    original = parameters()
    created = service.create_watch("a", original)
    replay = service.create_watch(
        "a",
        parameters(order_id=" ord-002 ", deadline_at="2026-04-01T08:00:00Z"),
    )
    assert created == replay
    assert set(created) == {"ok", "status", "id", "data", "error_code", "message"}
    assert created["ok"] and created["status"] == "created"
    assert created["data"]["deadline_at"] == NOW.isoformat()
    assert original == parameters()
    assert len(repo.list_watches("a")) == 1
    json.dumps(created, allow_nan=False)
    assert action.call_count == 2


def test_distinct_actions_can_create_identical_watches(setup):
    repo, action, service = setup
    first = service.create_watch("a", parameters())
    action.return_value = "confirmed-action-2"
    second = service.create_watch("a", parameters())
    assert first["id"] != second["id"]
    assert len(repo.list_watches("a")) == 2


def test_action_parameter_conflict_does_not_change_original(setup):
    repo, _, service = setup
    first = service.create_watch("a", parameters())
    conflict = service.create_watch("a", parameters(check_interval_minutes=30))
    assert not conflict["ok"] and conflict["error_code"] == "CONFLICT"
    assert [w.id for w in repo.list_watches("a")] == [first["id"]]
    assert service.create_watch("a", parameters()) == first


def test_replay_survives_service_recreation_with_shared_repository(setup):
    repo, action, service = setup
    first = service.create_watch("a", parameters())
    another = WatchService(repo, action_id_provider=action)
    assert another.create_watch("a", parameters()) == first
    # A new repository has no durable state or retry bindings.
    assert InMemoryWatchRepository().list_watches("a") == []


def test_cancellation_is_idempotent_and_keeps_retry_tombstone(setup):
    repo, _, service = setup
    first = service.create_watch("a", parameters())
    cancelled = service.cancel_watch("a", first["id"])
    assert cancelled["ok"] and cancelled["status"] == "cancelled"
    assert service.cancel_watch("a", first["id"]) == cancelled
    assert service.list_watches("a") == []
    assert repo.get_watch("a", first["id"]) is not None
    assert service.create_watch("a", parameters()) == first
    assert service.list_watches("a") == []


@pytest.mark.parametrize("workspace", [None, "", " ", 42, [], " a", "a "])
def test_invalid_workspace_rejected_before_writing_or_reading(workspace, setup):
    repo, action, service = setup
    assert service.create_watch(workspace, parameters())["error_code"] == "INVALID_ARGUMENT"
    assert service.cancel_watch(workspace, str(uuid4()))["error_code"] == "INVALID_ARGUMENT"
    for method in [service.list_watches, service.list_alerts, repo.list_checks]:
        with pytest.raises(WatchValidationError):
            method(workspace)
    assert repo.list_watches("a") == []
    action.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {},
        parameters(check_interval_minutes=0),
        parameters(check_interval_minutes=True),
        parameters(order_id=""),
        parameters(condition_type="production_drop"),
        parameters(deadline_at=None),
        parameters(deadline_at="2026-04-01T08:00:00+08:00"),
        parameters(confirmed_action_id="attempted-public-slot"),
    ],
)
def test_bad_parameters_reuse_phase1_validation(invalid, setup):
    repo, action, service = setup
    result = service.create_watch("a", invalid)
    assert not result["ok"]
    assert result["error_code"] in {"INVALID_ARGUMENT", "MISSING_FIELD", "UNSUPPORTED_CONDITION"}
    assert repo.list_watches("a") == []
    action.assert_not_called()


@pytest.mark.parametrize("action_id", [None, "", " ", 3, [], " action "])
def test_missing_or_invalid_internal_action_id_fails_closed(action_id):
    repo = InMemoryWatchRepository()
    service = WatchService(repo, action_id_provider=Mock(return_value=action_id))
    assert service.create_watch("a", parameters())["error_code"] == "INVALID_ARGUMENT"
    assert repo.list_watches("a") == []


def test_default_service_has_no_implicit_confirmation_context():
    repo = InMemoryWatchRepository()
    assert not WatchService(repo).create_watch("a", parameters())["ok"]
    assert repo.list_watches("a") == []


@pytest.mark.parametrize("watch_id", [None, "", "not-uuid", 3, []])
def test_invalid_cancel_identifier(watch_id, setup):
    _, _, service = setup
    assert service.cancel_watch("a", watch_id)["error_code"] == "INVALID_ARGUMENT"


def test_missing_records_and_workspace_isolation(setup):
    repo, _, service = setup
    first = service.create_watch("a", parameters())
    second = service.create_watch("b", parameters())
    assert first["id"] != second["id"]
    assert service.list_watches("a") == [first["data"]]
    assert service.list_watches("b") == [second["data"]]
    assert service.list_watches("empty") == []
    assert service.list_alerts("empty") == []
    unknown = service.cancel_watch("b", first["id"])
    missing = service.cancel_watch("b", str(uuid4()))
    assert unknown == missing
    assert missing["error_code"] == "NOT_FOUND"
    assert repo.get_watch("b", first["id"]) is None
    assert repo.list_watches("a")[0].id == first["id"]


def test_status_condition_and_equivalent_optional_parameters(setup):
    _, _, service = setup
    values = {
        "order_id": "ORD-002",
        "condition_type": "status_equals",
        "expected_status": "COMPLETE",
        "check_interval_minutes": 1,
    }
    created = service.create_watch("a", values)
    assert created["ok"]
    assert service.create_watch("a", {**values, "deadline_at": None}) == created
    assert (
        service.create_watch("a", {**values, "expected_status": "PACKING"})["error_code"]
        == "INVALID_ARGUMENT"
    )


def record(watch_id, *, workspace="a", alert=False):
    cls = Alert if alert else CheckRecord
    return cls(
        id=str(uuid4()),
        workspace_id=workspace,
        watch_id=watch_id,
        timestamp=NOW,
        observed_value={"status": "IN_PROGRESS"},
        evidence=(Evidence("orders.csv", "ORD-002", "Mock snapshot"),),
        outcome=CheckOutcome.MATCHED,
    )


@pytest.mark.parametrize("alert", [False, True])
def test_repository_record_isolation_append_only_and_detached_snapshots(alert, setup):
    repo, _, service = setup
    watch_id = service.create_watch("a", parameters())["id"]
    item = record(watch_id, alert=alert)
    save = repo.save_alert if alert else repo.save_check
    read = repo.list_alerts if alert else repo.list_checks
    save(item)
    save(item)  # Exact record retry is a no-op.
    assert len(read("a")) == 1 and read("b") == []
    with pytest.raises(RepositoryConflict):
        save(replace(item, observed_value={"status": "COMPLETE"}))
    with pytest.raises(WatchValidationError) as exc:
        save(replace(item, id=str(uuid4()), workspace_id="b"))
    assert exc.value.error_code == "NOT_FOUND"
    with pytest.raises(WatchValidationError):
        save(replace(item, id=str(uuid4()), watch_id=str(uuid4())))
    item.observed_value["status"] = "COMPLETE"
    listed = read("a")
    listed[0].observed_value["status"] = "CHANGED"
    listed.clear()
    assert read("a")[0].observed_value == {"status": "IN_PROGRESS"}
    service.cancel_watch("a", watch_id)
    assert len(read("a")) == 1
    with pytest.raises(RepositoryConflict):
        save(record(watch_id, alert=alert))


def test_record_revalidation_and_id_collisions(setup):
    repo, _, service = setup
    watch_id = service.create_watch("a", parameters())["id"]
    item = record(watch_id)
    item.observed_value["bad"] = float("nan")
    with pytest.raises(WatchValidationError):
        repo.save_check(item)
    with pytest.raises(RepositoryConflict):
        repo.save_check(replace(record(watch_id), id=watch_id))
    with pytest.raises(WatchValidationError):
        repo.save_check(record(watch_id, alert=True))
    with pytest.raises(WatchValidationError):
        repo.save_alert(record(watch_id))


def test_output_is_detached_and_json_serializable(setup):
    repo, _, service = setup
    created = service.create_watch("a", parameters())
    watch_id = created["id"]
    repo.save_alert(record(watch_id, alert=True))
    created["data"]["order_id"] = "CHANGED"
    listed = service.list_watches("a")
    listed[0]["order_id"] = "CHANGED"
    alerts = service.list_alerts("a")
    json.dumps(alerts, allow_nan=False)
    assert alerts[0]["timestamp"] == NOW.isoformat()
    assert alerts[0]["outcome"] == "matched"
    alerts[0]["observed_value"]["status"] = "CHANGED"
    alerts[0]["evidence"].clear()
    assert service.list_watches("a")[0]["order_id"] == "ORD-002"
    assert service.list_alerts("a")[0]["evidence"]
    assert service.list_alerts("a")[0]["observed_value"] == {"status": "IN_PROGRESS"}


def test_concurrent_retries_share_atomic_creation_binding(setup):
    repo, _, service = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.create_watch("a", parameters()), range(24)))
    assert all(result == results[0] for result in results)
    assert len(repo.list_watches("a")) == 1


def test_context_provider_keeps_parallel_action_identities_separate():
    context = ContextVar("p5_mock_action", default=None)
    repo = InMemoryWatchRepository()
    service = WatchService(repo, action_id_provider=context.get)

    def create(action_id):
        token = context.set(action_id)
        try:
            first = service.create_watch("a", parameters())
            assert service.create_watch("a", parameters()) == first
            return first
        finally:
            context.reset(token)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, ["action-1", "action-2", "action-3", "action-4"]))
    assert len({r["id"] for r in results}) == 4
    assert len(repo.list_watches("a")) == 4
    assert context.get() is None


def test_watch_uuid_collision_does_not_bind_action(setup):
    repo, action, service = setup
    first = service.create_watch("a", parameters())
    action.return_value = "action-2"
    collision = WatchService(repo, action_id_provider=action, id_factory=lambda: first["id"])
    assert collision.create_watch("a", parameters())["error_code"] == "CONFLICT"
    assert service.create_watch("a", parameters())["ok"]
    assert len(repo.list_watches("a")) == 2


def test_repository_rejects_invalid_direct_calls():
    repo = InMemoryWatchRepository()
    with pytest.raises(WatchValidationError):
        repo.create_watch(None, "action")
    candidate = Watch(
        id=str(uuid4()),
        workspace_id="a",
        order_id="ORD-002",
        condition_type="status_equals",
        expected_status="COMPLETE",
        check_interval_minutes=1,
    )
    for action_id in [None, "", " "]:
        with pytest.raises(WatchValidationError):
            repo.create_watch(candidate, action_id)


def test_uuid_spellings_cannot_bypass_cancellation_or_uniqueness(setup):
    repo, _, service = setup
    first = service.create_watch("a", parameters())
    watch_id = first["id"]
    alternate = watch_id.upper().replace("-", "")
    assert repo.get_watch("a", alternate).id == watch_id
    assert repo.cancel_watch("a", alternate).id == watch_id
    assert repo.list_watches("a") == []
    assert service.create_watch("a", parameters()) == first
    with pytest.raises(RepositoryConflict):
        repo.create_watch(replace(repo.get_watch("a", watch_id), id=alternate), "another-action")


def test_record_uuid_spellings_are_normalized(setup):
    repo, _, service = setup
    watch_id = service.create_watch("a", parameters())["id"]
    item = record(watch_id)
    alternate = replace(
        item,
        id=item.id.upper().replace("-", ""),
        watch_id=watch_id.upper().replace("-", ""),
    )
    repo.save_check(alternate)
    repo.save_check(item)
    assert repo.list_checks("a") == [item]


@pytest.mark.parametrize("method", ["create_watch", "cancel_watch", "list_watches", "list_alerts"])
def test_storage_errors_do_not_become_success_or_empty_data(method):
    repo = Mock()
    getattr(repo, method).side_effect = RuntimeError("private implementation details")
    service = WatchService(repo, action_id_provider=lambda: "confirmed-action")
    if method == "create_watch":
        result = service.create_watch("a", parameters())
    elif method == "cancel_watch":
        result = service.cancel_watch("a", str(uuid4()))
    else:
        with pytest.raises(RepositoryError, match="Unable to list") as exc:
            getattr(service, method)("a")
        assert "private" not in str(exc.value)
        return
    assert not result["ok"] and result["error_code"] == "STORAGE_ERROR"
    assert "private" not in json.dumps(result)


def test_context_failure_prevents_any_storage_call():
    repo = Mock()
    service = WatchService(repo, action_id_provider=Mock(side_effect=RuntimeError("private")))
    result = service.create_watch("a", parameters())
    assert result["error_code"] == "EXECUTION_ERROR"
    assert "private" not in json.dumps(result)
    repo.create_watch.assert_not_called()
