"""Tests for the P3/P4 briefing action adapter."""

from dataclasses import dataclass

import pytest

from app.agent.proactive.actions.models import ActionError
from app.agent.proactive.adapters.briefing_actions import BriefingActionExecutor


class FakeBriefingService:
    """Minimal fake for testing adapter validation."""

    def __init__(self):
        self.validation_calls = []
        self.write_calls = 0

    def _validate_parameters(self, parameters):
        self.validation_calls.append(parameters)

        if parameters.get("interval_days") != 1:
            raise ValueError("Invalid interval")

        if parameters.get("local_time") != "09:00":
            raise ValueError("Invalid time")

        return parameters


@pytest.fixture
def adapter():
    return BriefingActionExecutor(FakeBriefingService())


def create_command(parameters=None):
    return {
        "task_type": "briefing",
        "command_type": "create",
        "parameters": parameters
        if parameters is not None
        else {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
        },
        "target_id": None,
    }


def test_valid_create_passes_without_writing(adapter):
    result = adapter.validate("group02", create_command())

    assert result is None
    assert len(adapter.briefing_service.validation_calls) == 1
    assert adapter.briefing_service.write_calls == 0


def test_invalid_parameters_are_rejected(adapter):
    command = create_command({"interval_days": -1, "local_time": "09:00"})

    with pytest.raises(ActionError, match="INVALID_ARGUMENT"):
        adapter.validate("group02", command)

    assert adapter.briefing_service.write_calls == 0


def test_wrong_task_type_is_rejected(adapter):
    command = create_command()
    command["task_type"] = "watch"

    with pytest.raises(ActionError, match="INVALID_ARGUMENT"):
        adapter.validate("group02", command)

    assert adapter.briefing_service.write_calls == 0




@dataclass
class FakeSchedule:
    id: str = "schedule-001"
    workspace_id: str = "group02"
    interval_days: int = 1
    local_time: str = "09:00"
    timezone: str = "Asia/Singapore"
    start_date: str = "2026-11-09"
    enabled: bool = True
    cancelled_at: str | None = None
    config_version: int = 1


class FakeRepository:
    def __init__(self):
        self.schedule = FakeSchedule()
        self.read_calls = 0
        self.write_calls = 0

    def get_schedule(self, workspace_id, schedule_id):
        self.read_calls += 1

        if (
            workspace_id != self.schedule.workspace_id
            or schedule_id != self.schedule.id
        ):
            return None

        return self.schedule


def test_update_returns_target_version_without_writing():
    service = FakeBriefingService()
    service.repository = FakeRepository()
    adapter = BriefingActionExecutor(service)

    cmd = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": "schedule-001",
        "parameters": {"interval_days": 1},
    }

    assert adapter.validate("group02", cmd) == "1"
    assert service.repository.read_calls == 1
    assert service.repository.write_calls == 0


def test_cancel_returns_target_version_without_writing():
    service = FakeBriefingService()
    service.repository = FakeRepository()
    adapter = BriefingActionExecutor(service)

    cmd = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": "schedule-001",
        "parameters": {},
    }

    assert adapter.validate("group02", cmd) == "1"
    assert service.repository.read_calls == 1
    assert service.repository.write_calls == 0


def test_cancelled_schedule_cannot_be_updated():
    service = FakeBriefingService()
    service.repository = FakeRepository()
    service.repository.schedule.cancelled_at = "2026-11-08T10:00:00+00:00"
    adapter = BriefingActionExecutor(service)

    cmd = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": "schedule-001",
        "parameters": {"interval_days": 1},
    }

    with pytest.raises(ActionError, match="CONFLICT"):
        adapter.validate("group02", cmd)

    assert service.repository.write_calls == 0

def test_update_missing_target_is_rejected():
    service = FakeBriefingService()
    service.repository = FakeRepository()
    adapter = BriefingActionExecutor(service)

    cmd = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": "unknown-schedule",
        "parameters": {"interval_days": 1},
    }

    with pytest.raises(ActionError, match="NOT_FOUND"):
        adapter.validate("group02", cmd)

    assert service.repository.write_calls == 0


def test_cross_workspace_cancel_is_rejected():
    service = FakeBriefingService()
    service.repository = FakeRepository()
    adapter = BriefingActionExecutor(service)

    cmd = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": "schedule-001",
        "parameters": {},
    }

    with pytest.raises(ActionError, match="NOT_FOUND"):
        adapter.validate("another-workspace", cmd)

    assert service.repository.write_calls == 0


def test_missing_config_version_fails_closed():
    service = FakeBriefingService()
    service.repository = FakeRepository()
    service.repository.schedule.config_version = None
    adapter = BriefingActionExecutor(service)

    cmd = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": "schedule-001",
        "parameters": {},
    }

    with pytest.raises(ActionError, match="CONFLICT"):
        adapter.validate("group02", cmd)

    assert service.repository.write_calls == 0

def test_unknown_repository_value_error_maps_to_execution_error():
    from app.agent.proactive.actions.models import ActionError

    class BrokenRepository:
        def execute_confirmed_action(
            self,
            workspace_id,
            command,
            *,
            idempotency_key,
            expected_version,
        ):
            raise ValueError("unexpected internal failure")

    service = FakeBriefingService()
    service.repository = BrokenRepository()
    adapter = BriefingActionExecutor(service)

    command = create_command()

    with pytest.raises(ActionError) as exc_info:
        adapter.execute(
            "group02",
            command,
            idempotency_key="error-test-001",
            expected_version=None,
        )

    assert exc_info.value.code == "EXECUTION_ERROR"
