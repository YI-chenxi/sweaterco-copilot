"""Targeted cancellation error-mapping regressions; no P4 imports or APIs."""

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent.proactive.watches.models import Watch
from app.agent.proactive.watches.repository import RepositoryConflict, RepositoryError
from app.agent.proactive.watches.service import WatchService


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (RepositoryConflict("private version conflict"), "conflict", "CONFLICT"),
        (RepositoryError("private storage detail"), "error", "STORAGE_ERROR"),
        (RuntimeError("private implementation detail"), "error", "STORAGE_ERROR"),
        (OSError("private disk detail"), "error", "STORAGE_ERROR"),
    ],
)
def test_cancel_maps_conflict_separately_and_keeps_storage_errors(failure, status, code):
    repository = Mock()
    repository.cancel_watch.side_effect = failure
    service = WatchService(repository)
    watch_id = str(uuid4())

    result = service.cancel_watch("workspace-a", watch_id.upper())

    assert result == {
        "ok": False,
        "status": status,
        "id": None,
        "data": {},
        "error_code": code,
        "message": (
            "Watch conflicts with the requested cancellation."
            if code == "CONFLICT"
            else "Unable to cancel watch."
        ),
    }
    assert "private" not in str(result)
    repository.cancel_watch.assert_called_once_with("workspace-a", watch_id)
    repository.create_watch.assert_not_called()


def test_missing_watch_keeps_not_found_result():
    repository = Mock()
    repository.cancel_watch.return_value = None
    result = WatchService(repository).cancel_watch("workspace-a", str(uuid4()))
    assert result == {
        "ok": False,
        "status": "not_found",
        "id": None,
        "data": {},
        "error_code": "NOT_FOUND",
        "message": "Watch not found.",
    }


@pytest.mark.parametrize(
    "workspace,watch_id",
    [
        ("", str(uuid4())),
        (" workspace-a", str(uuid4())),
        (None, str(uuid4())),
        ("workspace-a", "invalid-uuid"),
        ("workspace-a", None),
        ("workspace-a", []),
    ],
)
def test_bad_cancel_arguments_do_not_reach_repository(workspace, watch_id):
    repository = Mock()
    result = WatchService(repository).cancel_watch(workspace, watch_id)
    assert result["ok"] is False
    assert result["status"] == "invalid"
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["id"] is None and result["data"] == {}
    repository.cancel_watch.assert_not_called()


def test_successful_cancel_keeps_result_and_utc_serialization():
    repository = Mock()
    watch_id = str(uuid4())
    deadline = datetime(2026, 4, 2, 8, tzinfo=timezone.utc)
    repository.cancel_watch.return_value = Watch(
        id=watch_id,
        workspace_id="workspace-a",
        order_id="ORD-002",
        condition_type="no_progress_by_deadline",
        check_interval_minutes=15,
        deadline_at=deadline,
    )
    repository.get_receipt.return_value = {
        "ok": True,
        "status": "cancelled",
        "id": watch_id,
        "error_code": None,
        "message": "Watch cancelled.",
        "data": {
            "id": watch_id,
            "workspace_id": "workspace-a",
            "order_id": "ORD-002",
            "condition_type": "no_progress_by_deadline",
            "check_interval_minutes": 15,
            "deadline_at": deadline.isoformat(),
            "expected_status": None,
        },
    }
    service = WatchService(repository)
    result = service.cancel_watch("workspace-a", watch_id)
    assert result["ok"] is True and result["status"] == "cancelled"
    assert result["id"] == watch_id and result["error_code"] is None
    assert result["data"]["deadline_at"] == deadline.isoformat()
    assert service.cancel_watch("workspace-a", watch_id) == result


def test_create_conflict_mapping_is_unchanged():
    repository = Mock()
    repository.create_watch.side_effect = RepositoryConflict("private action conflict")
    service = WatchService(repository, action_id_provider=lambda: "mock-p4-execution-key")
    result = service.create_watch(
        "workspace-a",
        {
            "order_id": "ORD-002",
            "condition_type": "status_equals",
            "expected_status": "COMPLETE",
            "check_interval_minutes": 15,
        },
    )
    assert result["ok"] is False and result["status"] == "conflict"
    assert result["error_code"] == "CONFLICT"
    assert "private" not in str(result)
