"""Tests for the P5/P4 watch action adapter."""

from types import SimpleNamespace

import pytest

from app.agent.proactive.actions.models import ActionError
from app.agent.proactive.adapters.watch_actions import WatchActionExecutor


class FakeRepository:
    def __init__(self):
        self.state = SimpleNamespace(version="1", cancelled=False)
        self.read_calls = 0

    def get_watch_state(self, workspace_id, watch_id):
        self.read_calls += 1
        if workspace_id != "group02" or watch_id != "watch-001":
            return None
        return self.state


class FakeService:
    def __init__(self):
        self.repository = FakeRepository()


def test_cancel_returns_version():
    adapter = WatchActionExecutor(FakeService())

    command = {
        "task_type": "watch",
        "command_type": "cancel",
        "target_id": "watch-001",
        "parameters": {},
    }

    assert adapter.validate("group02", command) == "1"
    assert adapter.watch_service.repository.read_calls == 1


def test_cancelled_watch_is_rejected():
    service = FakeService()
    service.repository.state.cancelled = True
    adapter = WatchActionExecutor(service)

    command = {
        "task_type": "watch",
        "command_type": "cancel",
        "target_id": "watch-001",
        "parameters": {},
    }

    with pytest.raises(ActionError) as exc:
        adapter.validate("group02", command)

    assert exc.value.code == "CONFLICT"


def create_command(parameters=None):
    return {
        "task_type": "watch",
        "command_type": "create",
        "target_id": None,
        "parameters": parameters if parameters is not None else {
            "order_id": "ORD-057",
            "condition_type": "status_equals",
            "expected_status": "COMPLETE",
            "check_interval_minutes": 30,
        },
    }


def test_valid_create_passes_without_writing():
    adapter = WatchActionExecutor(FakeService())

    assert adapter.validate("group02", create_command()) is None
    assert adapter.watch_service.repository.read_calls == 0


def test_missing_required_field_is_rejected():
    adapter = WatchActionExecutor(FakeService())

    command = create_command({
        "order_id": "ORD-057",
        "condition_type": "status_equals",
    })

    with pytest.raises(ActionError) as exc:
        adapter.validate("group02", command)

    assert exc.value.code == "MISSING_FIELD"


def test_unsupported_condition_is_rejected():
    adapter = WatchActionExecutor(FakeService())

    command = create_command({
        "order_id": "ORD-057",
        "condition_type": "unknown_condition",
        "check_interval_minutes": 30,
    })

    with pytest.raises(ActionError) as exc:
        adapter.validate("group02", command)

    assert exc.value.code == "UNSUPPORTED_CONDITION"
