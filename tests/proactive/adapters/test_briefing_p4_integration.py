"""Integration tests for P4 confirmed actions and P3 briefing."""

import sqlite3

from app.agent.proactive.actions.repository import SQLiteActionRepository
from app.agent.proactive.actions.service import ActionService
from app.agent.proactive.adapters.briefing_actions import BriefingActionExecutor
from app.agent.proactive.briefing.service import BriefingService
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository


def test_briefing_create_requires_confirmation(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")

    adapter = BriefingActionExecutor(BriefingService(briefing_repo))

    service = ActionService(
        action_repo,
        {
            ("briefing", "create"): adapter,
            ("briefing", "update"): adapter,
            ("briefing", "cancel"): adapter,
        },
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

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
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)

    assert proposed["ok"] is True

    with sqlite3.connect(briefing_repo.database_path) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

    assert before == 0, "Proposal must not create a schedule"

    proposal_id = proposed["id"]
    confirmed = service.confirm_action("group02", proposal_id)

    assert confirmed["ok"] is True, confirmed

    with sqlite3.connect(briefing_repo.database_path) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

    assert after == 1, "Confirmation must create exactly one schedule"


def test_repeated_confirmation_does_not_duplicate_briefing(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")

    adapter = BriefingActionExecutor(BriefingService(briefing_repo))

    service = ActionService(
        action_repo,
        {("briefing", "create"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

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
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True

    first = service.confirm_action("group02", proposed["id"])
    second = service.confirm_action("group02", proposed["id"])

    assert first["ok"] is True
    assert second["ok"] is True

    with sqlite3.connect(briefing_repo.database_path) as connection:
        schedules = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

        receipts = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts"
        ).fetchone()[0]

    assert schedules == 1
    assert receipts == 1


def test_rejected_briefing_proposal_does_not_execute(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")

    adapter = BriefingActionExecutor(BriefingService(briefing_repo))

    service = ActionService(
        action_repo,
        {("briefing", "create"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

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
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True

    rejected = service.reject_action("group02", proposed["id"])
    assert rejected["ok"] is True

    # A rejected proposal must never execute.
    service.confirm_action("group02", proposed["id"])

    with sqlite3.connect(briefing_repo.database_path) as connection:
        schedules = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

        receipts = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts"
        ).fetchone()[0]

    assert schedules == 0
    assert receipts == 0


def test_confirmed_briefing_has_audit_history(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")

    adapter = BriefingActionExecutor(BriefingService(briefing_repo))

    service = ActionService(
        action_repo,
        {("briefing", "create"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

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
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True

    confirmed = service.confirm_action("group02", proposed["id"])
    assert confirmed["ok"] is True

    history = service.get_action_history("group02")

    assert isinstance(history, list)

    records = [
        record
        for record in history
        if record["proposal_id"] == proposed["id"]
    ]

    assert records
    assert all(record["workspace_id"] == "group02" for record in records)
    assert all(record["actor_id"] == "operator" for record in records)

    executed = [
        record
        for record in records
        if record["status"] == "executed"
    ]

    assert executed
    assert any(record["result"] is not None for record in executed)
    assert all(record["error_code"] is None for record in executed)


def test_briefing_update_requires_confirmation(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")
    briefing_service = BriefingService(briefing_repo)

    created = briefing_service.create_schedule(
        "group02",
        {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    )
    assert created["ok"] is True
    schedule_id = created["id"]

    adapter = BriefingActionExecutor(briefing_service)

    service = ActionService(
        action_repo,
        {("briefing", "update"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

    command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True, proposed

    before = briefing_repo.get_schedule("group02", schedule_id)
    assert before.interval_days == 1
    assert before.config_version == 1

    confirmed = service.confirm_action("group02", proposed["id"])
    assert confirmed["ok"] is True, confirmed

    after = briefing_repo.get_schedule("group02", schedule_id)
    assert after.interval_days == 3
    assert after.config_version == 2

    repeated = service.confirm_action("group02", proposed["id"])
    assert repeated["ok"] is True

    final = briefing_repo.get_schedule("group02", schedule_id)
    assert final.config_version == 2


def test_briefing_cancel_requires_confirmation(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")
    briefing_service = BriefingService(briefing_repo)

    created = briefing_service.create_schedule(
        "group02",
        {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    )
    assert created["ok"] is True
    schedule_id = created["id"]

    adapter = BriefingActionExecutor(briefing_service)

    service = ActionService(
        action_repo,
        {("briefing", "cancel"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

    command = {
        "task_type": "briefing",
        "command_type": "cancel",
        "target_id": schedule_id,
        "parameters": {},
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True, proposed

    before = briefing_repo.get_schedule("group02", schedule_id)
    assert before.enabled is True
    assert before.cancelled_at is None
    assert before.config_version == 1

    confirmed = service.confirm_action("group02", proposed["id"])
    assert confirmed["ok"] is True, confirmed

    after = briefing_repo.get_schedule("group02", schedule_id)
    assert after.enabled is False
    assert after.next_run_at is None
    assert after.cancelled_at is not None
    assert after.config_version == 2

    repeated = service.confirm_action("group02", proposed["id"])
    assert repeated["ok"] is True

    final = briefing_repo.get_schedule("group02", schedule_id)
    assert final.config_version == 2

    history = service.get_action_history("group02")
    records = [
        record
        for record in history
        if record["proposal_id"] == proposed["id"]
    ]

    assert records
    assert any(
        record["status"] == "executed"
        and record["action_type"] == "briefing.cancel"
        for record in records
    )


def test_stale_briefing_proposal_cannot_execute(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")
    briefing_service = BriefingService(briefing_repo)
    adapter = BriefingActionExecutor(briefing_service)

    service = ActionService(
        action_repo,
        {("briefing", "update"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

    created = briefing_service.create_schedule(
        "group02",
        {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-09",
            "enabled": True,
        },
    )
    assert created["ok"] is True
    schedule_id = created["id"]

    command = {
        "task_type": "briefing",
        "command_type": "update",
        "target_id": schedule_id,
        "parameters": {"interval_days": 3},
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True

    # Another operation changes the schedule before confirmation.
    changed = briefing_service.update_schedule(
        "group02",
        schedule_id,
        {"interval_days": 2},
    )
    assert changed["ok"] is True

    current = briefing_repo.get_schedule("group02", schedule_id)
    assert current.config_version == 2

    # The original proposal must not overwrite the newer version.
    confirmed = service.confirm_action("group02", proposed["id"])

    assert confirmed["ok"] is False

    final = briefing_repo.get_schedule("group02", schedule_id)
    assert final.interval_days == 2
    assert final.config_version == 2

    history = service.get_action_history("group02")
    records = [
        record for record in history
        if record["proposal_id"] == proposed["id"]
    ]

    assert records
    assert not any(record["status"] == "executed" for record in records)


def test_committed_briefing_with_lost_receipt_is_not_reexecuted(tmp_path):
    briefing_repo = SQLiteBriefingRepository(tmp_path / "briefing.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")

    class LostReceiptExecutor(BriefingActionExecutor):
        def __init__(self, briefing_service):
            super().__init__(briefing_service)
            self.execution_calls = 0

        def execute(
            self,
            workspace_id,
            command,
            *,
            idempotency_key,
            expected_version,
        ):
            self.execution_calls += 1

            super().execute(
                workspace_id,
                command,
                idempotency_key=idempotency_key,
                expected_version=expected_version,
            )

            # Simulate losing the response after a successful commit.
            raise ConnectionError("simulated lost receipt")

    adapter = LostReceiptExecutor(BriefingService(briefing_repo))

    service = ActionService(
        action_repo,
        {("briefing", "create"): adapter},
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

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
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True

    first = service.confirm_action("group02", proposed["id"])

    assert first["ok"] is False

    history = service.get_action_history("group02")
    records = [
        record
        for record in history
        if record["proposal_id"] == proposed["id"]
    ]

    assert any(
        record["result"] is not None
        and record["result"].get("outcome_unknown") is True
        for record in records
    )

    # A second confirmation must not invoke the business executor again.
    service.confirm_action("group02", proposed["id"])

    assert adapter.execution_calls == 1

    with sqlite3.connect(briefing_repo.database_path) as connection:
        schedules = connection.execute(
            "SELECT COUNT(*) FROM briefing_schedules"
        ).fetchone()[0]

        receipts = connection.execute(
            "SELECT COUNT(*) FROM briefing_action_receipts"
        ).fetchone()[0]

    assert schedules == 1
    assert receipts == 1
