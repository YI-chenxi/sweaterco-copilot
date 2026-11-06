"""P2 contract tests against deterministic P3/P4/P5 service fakes.

These verify mockable service contracts and failure behavior; they do not prove
that the separate feature branches or an integrated deployment meet them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.proactive.mock_services import MockActionService, MockBriefingService, MockWatchService

UTC = timezone.utc


def dt(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=UTC)


def briefing_params(interval: int = 1) -> dict:
    return {
        "interval_days": interval,
        "local_time": "08:30",
        "timezone": "Asia/Singapore",
        "start_date": "2026-11-06",
    }


def create_briefing(service: MockBriefingService, interval: int = 1) -> str:
    result = service.create_schedule("workspace-a", briefing_params(interval))
    assert result["ok"]
    return result["id"]


@pytest.mark.parametrize("interval", [1, 3, 7])
def test_briefing_accepts_supported_positive_intervals(interval: int) -> None:
    assert MockBriefingService().create_schedule("w", briefing_params(interval))["ok"]


@pytest.mark.parametrize("interval", [0, -1, 1.5, True, "3", None])
def test_briefing_rejects_invalid_interval_without_persisting(interval: object) -> None:
    service = MockBriefingService()
    params = briefing_params()
    params["interval_days"] = interval
    result = service.create_schedule("w", params)
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert not service.schedules


@pytest.mark.parametrize(
    ("field", "value"),
    [("local_time", "25:00"), ("start_date", "tomorrow"), ("timezone", "Mars/Olympus")],
)
def test_briefing_rejects_invalid_time_date_and_timezone(field: str, value: str) -> None:
    service = MockBriefingService()
    params = briefing_params()
    params[field] = value
    assert service.create_schedule("w", params)["error_code"] == "INVALID_ARGUMENT"
    assert not service.schedules


def test_briefing_missing_workspace_is_not_authorized() -> None:
    assert (
        MockBriefingService().create_schedule("", briefing_params())["error_code"] == "UNAUTHORIZED"
    )


def test_briefing_storage_failure_does_not_leave_partial_schedule() -> None:
    service = MockBriefingService(fail_next_save=True)
    assert service.create_schedule("w", briefing_params())["error_code"] == "STORAGE_ERROR"
    assert not service.schedules
    assert service.create_schedule("w", briefing_params())["ok"]


@pytest.mark.parametrize(
    ("interval", "after_day", "next_day"),
    [(1, 6, 7), (3, 6, 9), (7, 6, 13), (3, 10, 12), (7, 12, 13)],
)
def test_briefing_interval_is_anchored_to_start_date(
    interval: int, after_day: int, next_day: int
) -> None:
    service = MockBriefingService()
    schedule_id = create_briefing(service, interval)
    next_run = service.next_run(schedule_id, dt(after_day, 1))
    assert next_run == dt(next_day, 0, 30)  # 08:30 Singapore == 00:30 UTC


def test_briefing_is_not_due_one_second_before_occurrence() -> None:
    service = MockBriefingService()
    schedule_id = create_briefing(service)
    service.schedules[schedule_id]["next_run_at"] = dt(6, 0, 30)
    assert service.run_due_briefings(dt(6, 0, 29, 59)) == []


def test_briefing_runs_at_boundary_once_and_advances_schedule() -> None:
    service = MockBriefingService()
    schedule_id = create_briefing(service)
    due_at = dt(6, 0, 30)
    service.schedules[schedule_id]["next_run_at"] = due_at
    assert service.run_due_briefings(due_at - timedelta(seconds=1)) == []
    assert service.run_due_briefings(due_at)[0]["ok"]
    assert service.run_due_briefings(due_at) == []
    assert service.schedules[schedule_id]["next_run_at"] == due_at + timedelta(days=1)


def test_briefing_execution_failure_is_reported_and_retry_is_idempotent() -> None:
    service = MockBriefingService(fail_next_execute=True)
    schedule_id = create_briefing(service)
    due_at = dt(6, 0, 30)
    service.schedules[schedule_id]["next_run_at"] = due_at
    assert service.run_due_briefings(due_at)[0]["error_code"] == "EXECUTION_ERROR"
    assert service.run_due_briefings(due_at)[0]["ok"]
    assert service.run_due_briefings(due_at) == []
    run = service.runs[(schedule_id, due_at.isoformat())]
    assert run == {"status": "succeeded", "attempts": 2}


def test_briefing_cancel_and_workspace_scope() -> None:
    service = MockBriefingService()
    schedule_id = create_briefing(service)
    assert service.cancel_schedule("workspace-b", schedule_id)["error_code"] == "NOT_FOUND"
    assert service.cancel_schedule("workspace-a", schedule_id)["status"] == "cancelled"
    service.schedules[schedule_id]["next_run_at"] = dt(6, 0, 30)
    assert service.run_due_briefings(dt(7))[0:1] == []


def test_watch_rejects_unsupported_condition() -> None:
    service = MockWatchService()
    result = service.create_watch(
        "w", {"order_id": "ORD-1", "condition_type": "weather_changes", "check_interval_minutes": 5}
    )
    assert result["error_code"] == "UNSUPPORTED_CONDITION"
    assert not service.watches


@pytest.mark.parametrize(
    "params",
    [
        {
            "condition_type": "status_equals",
            "expected_status": "shipped",
            "check_interval_minutes": 5,
        },
        {"order_id": "ORD-1", "condition_type": "status_equals", "check_interval_minutes": 5},
        {
            "order_id": "ORD-1",
            "condition_type": "status_equals",
            "expected_status": "x",
            "check_interval_minutes": 0,
        },
        {
            "order_id": "ORD-1",
            "condition_type": "no_progress_by_deadline",
            "deadline_at": "2026-11-06T09:00:00",
            "check_interval_minutes": 5,
        },
    ],
)
def test_watch_rejects_incomplete_or_invalid_parameters(params: dict) -> None:
    service = MockWatchService()
    assert service.create_watch("w", params)["error_code"] == "INVALID_ARGUMENT"
    assert not service.watches


def test_watch_status_fixture_records_no_trigger_then_trigger_once() -> None:
    service = MockWatchService()
    result = service.create_watch(
        "w",
        {
            "order_id": "ORD-1",
            "condition_type": "status_equals",
            "expected_status": "shipped",
            "check_interval_minutes": 10,
        },
    )
    watch_id = result["id"]
    first = service.run_due_watches(dt(6), "in_production")
    assert first[0]["triggered"] is False
    assert first[0]["observed_status"] == "in_production"
    assert service.alerts == []
    second = service.run_due_watches(dt(6, 0, 10), "shipped")
    assert second[0]["triggered"] is True
    assert len(service.alerts) == 1
    assert service.alerts[0]["watch_id"] == watch_id


def test_watch_duplicate_check_timestamp_is_idempotent() -> None:
    service = MockWatchService()
    service.create_watch(
        "w",
        {
            "order_id": "ORD-1",
            "condition_type": "status_equals",
            "expected_status": "shipped",
            "check_interval_minutes": 10,
        },
    )
    first = service.run_due_watches(dt(6), "shipped")
    assert service.run_due_watches(dt(6), "in_production") == []
    assert service.alerts[0] == {key: value for key, value in first[0].items() if key != "ok"}


def test_watch_deadline_boundary_is_not_triggered_early() -> None:
    service = MockWatchService()
    result = service.create_watch(
        "w",
        {
            "order_id": "ORD-2",
            "condition_type": "no_progress_by_deadline",
            "deadline_at": "2026-11-06T00:30:00+00:00",
            "baseline_status": "in_production",
            "check_interval_minutes": 15,
        },
    )
    assert service.run_due_watches(dt(6, 0, 29), "in_production")[0]["triggered"] is False
    assert service.run_due_watches(dt(6, 0, 30), "in_production")[0]["triggered"] is True
    assert service.alerts[0]["watch_id"] == result["id"]


def test_watch_check_failure_can_retry_without_duplicate_alert() -> None:
    service = MockWatchService(fail_next_check=True)
    service.create_watch(
        "w",
        {
            "order_id": "ORD-3",
            "condition_type": "status_equals",
            "expected_status": "shipped",
            "check_interval_minutes": 10,
        },
    )
    assert service.run_due_watches(dt(6), "shipped")[0]["error_code"] == "EXECUTION_ERROR"
    assert service.run_due_watches(dt(6), "shipped")[0]["triggered"] is True
    assert len(service.alerts) == 1


def test_watch_cancel_and_workspace_scope() -> None:
    service = MockWatchService()
    watch_id = service.create_watch(
        "w-a",
        {
            "order_id": "ORD-4",
            "condition_type": "status_equals",
            "expected_status": "shipped",
            "check_interval_minutes": 10,
        },
    )["id"]
    assert service.cancel_watch("w-b", watch_id)["error_code"] == "NOT_FOUND"
    assert service.cancel_watch("w-a", watch_id)["status"] == "cancelled"
    assert service.run_due_watches(dt(6), "shipped") == []


def test_action_proposal_requires_workspace_and_exact_id() -> None:
    service = MockActionService()
    now = dt(6)
    assert (
        service.propose_action("", {"action_type": "cancel"}, now)["error_code"] == "UNAUTHORIZED"
    )
    proposal = service.propose_action("w-a", {"action_type": "cancel", "target_id": "ORD-1"}, now)
    assert service.confirm_action("w-b", proposal["id"], now)["error_code"] == "NOT_FOUND"
    assert service.confirm_action("w-a", "vague-yes", now)["error_code"] == "NOT_FOUND"
    assert service.effects == []


def test_action_confirmation_executes_once_and_writes_audit() -> None:
    service = MockActionService()
    now = dt(6)
    proposal_id = service.propose_action("w", {"action_type": "cancel", "target_id": "ORD-1"}, now)[
        "id"
    ]
    assert service.confirm_action("w", proposal_id, now)["status"] == "executed"
    assert service.confirm_action("w", proposal_id, now)["status"] == "already_executed"
    assert len(service.effects) == 1
    assert len(service.audit) == 1
    assert service.audit[0]["event"] == "executed"


def test_action_confirmation_expiry_boundary_blocks_side_effect() -> None:
    service = MockActionService()
    now = dt(6)
    proposal_id = service.propose_action("w", {"action_type": "update"}, now)["id"]
    assert (
        service.confirm_action("w", proposal_id, now + timedelta(minutes=14, seconds=59))["status"]
        == "executed"
    )
    expired_id = service.propose_action("w", {"action_type": "cancel"}, now)["id"]
    assert (
        service.confirm_action("w", expired_id, now + timedelta(minutes=15))["error_code"]
        == "EXPIRED"
    )
    assert len(service.effects) == 1


def test_action_rejection_is_terminal_and_audited() -> None:
    service = MockActionService()
    now = dt(6)
    proposal_id = service.propose_action("w", {"action_type": "cancel"}, now)["id"]
    assert service.reject_action("w", proposal_id)["status"] == "rejected"
    assert service.confirm_action("w", proposal_id, now)["error_code"] == "CONFLICT"
    assert service.effects == []
    assert service.audit == [{"proposal_id": proposal_id, "event": "rejected"}]


def test_action_execution_failure_can_retry_using_same_idempotency_key() -> None:
    service = MockActionService(fail_next_execute=True)
    now = dt(6)
    proposal_id = service.propose_action("w", {"action_type": "cancel"}, now)["id"]
    key = service.proposals[proposal_id]["idempotency_key"]
    assert service.confirm_action("w", proposal_id, now)["error_code"] == "EXECUTION_ERROR"
    assert not service.effects
    assert service.confirm_action("w", proposal_id, now)["status"] == "executed"
    assert service.effects == [(proposal_id, key)]
    assert len(service.audit) == 2


@pytest.mark.parametrize("value", [None, "2026-11-06T00:00:00", "not-a-date"])
def test_all_due_runners_require_aware_datetime(value: object) -> None:
    briefing = MockBriefingService()
    watch = MockWatchService()
    actions = MockActionService()
    with pytest.raises((TypeError, AttributeError, ValueError)):
        briefing.run_due_briefings(value)  # type: ignore[arg-type]
    with pytest.raises((TypeError, AttributeError, ValueError)):
        watch.run_due_watches(value, "shipped")  # type: ignore[arg-type]
    now = dt(6)
    proposal_id = actions.propose_action("w", {}, now)["id"]
    with pytest.raises((TypeError, AttributeError, ValueError)):
        actions.confirm_action("w", proposal_id, value)  # type: ignore[arg-type]
