"""Real P4/P5 watch integration tests."""


import pytest

from app.agent.proactive.actions.repository import SQLiteActionRepository
from app.agent.proactive.actions.service import ActionService
from app.agent.proactive.adapters.watch_actions import WatchActionExecutor
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository


@pytest.fixture
def env(tmp_path):
    watch_repo = SQLiteWatchRepository(tmp_path / "watches.db")
    action_repo = SQLiteActionRepository(tmp_path / "actions.db")
    adapter = WatchActionExecutor(WatchService(watch_repo))

    service = ActionService(
        action_repo,
        {
            ("watch", "create"): adapter,
            ("watch", "cancel"): adapter,
        },
        actor_provider=lambda: "operator",
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02"
        ),
    )

    yield service, watch_repo, adapter

    watch_repo.close()
    action_repo.close()


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
        "missing_fields": [],
        "language": "en",
    }


def test_create_requires_confirmation(env):
    service, repo, _ = env

    proposed = service.propose_action("group02", create_command())
    assert proposed["ok"] is True, proposed
    assert repo.list_watches("group02") == []

    confirmed = service.confirm_action("group02", proposed["id"])
    assert confirmed["ok"] is True, confirmed
    assert len(repo.list_watches("group02")) == 1


def test_repeated_create_confirmation_is_idempotent(env):
    service, repo, _ = env

    proposed = service.propose_action("group02", create_command())
    assert proposed["ok"] is True

    first = service.confirm_action("group02", proposed["id"])
    second = service.confirm_action("group02", proposed["id"])

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(repo.list_watches("group02")) == 1


def test_cancel_requires_confirmation(env):
    service, repo, _ = env

    proposed_create = service.propose_action("group02", create_command())
    assert proposed_create["ok"] is True
    created = service.confirm_action("group02", proposed_create["id"])
    assert created["ok"] is True

    watch_id = repo.list_watches("group02")[0].id

    command = {
        "task_type": "watch",
        "command_type": "cancel",
        "target_id": watch_id,
        "parameters": {},
        "missing_fields": [],
        "language": "en",
    }

    proposed = service.propose_action("group02", command)
    assert proposed["ok"] is True, proposed

    before = repo.get_watch_state("group02", watch_id)
    assert before.cancelled is False
    assert before.version == "1"

    confirmed = service.confirm_action("group02", proposed["id"])
    assert confirmed["ok"] is True, confirmed

    after = repo.get_watch_state("group02", watch_id)
    assert after.cancelled is True
    assert after.version == "2"

    repeated = service.confirm_action("group02", proposed["id"])
    assert repeated["ok"] is True
    assert repo.get_watch_state("group02", watch_id).version == "2"


def test_confirmed_create_has_audit_history(env):
    service, _, _ = env

    proposed = service.propose_action("group02", create_command())
    assert proposed["ok"] is True

    confirmed = service.confirm_action("group02", proposed["id"])
    assert confirmed["ok"] is True

    records = [
        item
        for item in service.get_action_history("group02")
        if item["proposal_id"] == proposed["id"]
    ]

    assert records
    assert any(
        item["status"] == "executed"
        and item["action_type"] == "watch.create"
        for item in records
    )
