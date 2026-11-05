"""JSON-type and confirmed-action retry regressions, shared by both P5 repositories."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.agent.contracts import Evidence
from app.agent.proactive.watches.memory_repository import InMemoryWatchRepository
from app.agent.proactive.watches.models import Alert, CheckOutcome, CheckRecord
from app.agent.proactive.watches.repository import RepositoryConflict
from app.agent.proactive.watches.serialization import dumps, record_data
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository

NOW = datetime(2026, 4, 1, 8, tzinfo=timezone.utc)
PARAMETERS = {
    "order_id": "ORD-002",
    "condition_type": "status_equals",
    "expected_status": "COMPLETE",
    "check_interval_minutes": 15,
}


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    value = (
        InMemoryWatchRepository(clock=lambda: NOW)
        if request.param == "memory"
        else SQLiteWatchRepository(tmp_path / "json-retries.db", clock=lambda: NOW)
    )
    yield value
    if request.param == "sqlite":
        value.close()


@pytest.fixture(params=[False, True], ids=["check", "alert"])
def records(repo, request):
    service = WatchService(repo, action_id_provider=lambda: "confirmed-record-fixture")
    created = service.create_watch("workspace-a", PARAMETERS)
    assert created["ok"]
    is_alert = request.param

    def make(observation):
        return (Alert if is_alert else CheckRecord)(
            id=str(uuid4()),
            workspace_id="workspace-a",
            watch_id=created["id"],
            timestamp=NOW,
            observed_value=observation,
            evidence=(Evidence("orders.csv", "ORD-002", "Controlled regression snapshot"),),
            outcome=CheckOutcome.MATCHED,
        )

    return (
        make,
        repo.save_alert if is_alert else repo.save_check,
        repo.list_alerts if is_alert else repo.list_checks,
    )


@pytest.mark.parametrize(
    "original,changed",
    [
        ({"value": True}, {"value": 1}),
        ({"value": False}, {"value": 0}),
        ({"value": 1}, {"value": 1.0}),
        (
            {"nested": [True, {"value": False}]},
            {"nested": [1, {"value": 0}]},
        ),
    ],
    ids=["true-vs-one", "false-vs-zero", "int-vs-float", "nested-bool-vs-int"],
)
def test_same_record_id_different_json_types_conflict(records, original, changed):
    make, save, read = records
    first = make(original)
    save(first)
    with pytest.raises(RepositoryConflict):
        save(replace(first, observed_value=changed))
    stored = read("workspace-a")
    assert len(stored) == 1
    # Python equality alone would hide the same bool/number defect in this assertion.
    assert dumps(record_data(stored[0])) == dumps(record_data(first))
    assert dumps(stored[0].observed_value) != dumps(changed)


def test_identical_record_retry_ignores_dictionary_key_order(records):
    make, save, read = records
    first = make({"nested": {"a": True, "b": [0, False]}})
    save(first)
    save(replace(first, observed_value={"nested": {"b": [0, False], "a": True}}))
    stored = read("workspace-a")
    assert len(stored) == 1
    assert dumps(record_data(stored[0])) == dumps(record_data(first))


def test_same_execution_key_identical_normalized_parameters_replays_original_receipt(repo):
    service = WatchService(repo, action_id_provider=lambda: "stable-confirmed-execution-key")
    first = service.create_watch("workspace-a", PARAMETERS)
    assert first["ok"]
    reordered = {
        "check_interval_minutes": 15,
        "expected_status": "COMPLETE",
        "condition_type": "status_equals",
        "order_id": " ord-002 ",
    }
    assert service.create_watch("workspace-a", reordered) == first
    assert repo.get_receipt("workspace-a", "stable-confirmed-execution-key") == first
    assert len(repo.list_watches("workspace-a")) == 1


def test_same_execution_key_changed_parameters_conflict_and_preserve_original_receipt(repo):
    service = WatchService(repo, action_id_provider=lambda: "stable-confirmed-execution-key")
    first = service.create_watch("workspace-a", PARAMETERS)
    assert first["ok"]
    conflict = service.create_watch("workspace-a", {**PARAMETERS, "check_interval_minutes": 30})
    assert conflict["ok"] is False
    assert conflict["status"] == "conflict" and conflict["error_code"] == "CONFLICT"
    assert repo.get_receipt("workspace-a", "stable-confirmed-execution-key") == first
    stored = repo.list_watches("workspace-a")
    assert len(stored) == 1 and stored[0].id == first["id"]
    assert stored[0].check_interval_minutes == 15
