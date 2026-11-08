import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.agent.proactive.actions.repository import SQLiteActionRepository
from app.agent.proactive.actions.service import ActionService
from app.agent.proactive.briefing.service import BriefingService
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository
from app.agent.proactive.integration import ProactiveApplication, build_proactive_application
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository


class Parser:
    def __init__(self, command):
        self.command = command

    def parse(self, _text, context=None):
        if self.command == "raise":
            raise RuntimeError("storage unavailable")
        return type("Parsed", (), {"to_dict": lambda _self: self.command})()


class Executor:
    def validate(self, _workspace, _command):
        return None

    def execute(self, _workspace, command, *, idempotency_key, expected_version):
        return {"ok": True, "status": "created", "id": str(uuid4()), "data": {}, "error_code": None}


@pytest.fixture
def app(tmp_path):
    actor, workspace = "operator-1", "workspace-1"
    action_repo = SQLiteActionRepository(tmp_path / "proactive.sqlite3")
    briefing_repo = SQLiteBriefingRepository(tmp_path / "proactive.sqlite3")
    watch_repo = SQLiteWatchRepository(tmp_path / "proactive.sqlite3")

    def policy(user, scope, operation, proposal):
        return user == actor and scope == workspace

    actions = ActionService(
        action_repo,
        {("briefing", "create"): Executor()},
        actor_provider=lambda: actor,
        authorizer=policy,
    )
    return ProactiveApplication(
        parser=Parser({}),
        actions=actions,
        briefings=BriefingService(briefing_repo),
        watches=WatchService(watch_repo),
        actor_provider=lambda: actor,
        workspace_provider=lambda: workspace,
    ), actions


def test_pending_and_detail_are_scoped_and_hide_claimed(tmp_path):
    actor, workspace = "operator", "workspace"
    repo = SQLiteActionRepository(tmp_path / "actions.db")
    service = ActionService(
        repo,
        {("briefing", "create"): Executor()},
        actor_provider=lambda: actor,
        authorizer=lambda *_args: True,
    )
    command = {
        "command_type": "create",
        "task_type": "briefing",
        "parameters": {"interval_days": 1},
        "target_id": None,
        "missing_fields": [],
        "language": "en",
    }
    proposal = service.propose_action(workspace, command)["data"]["proposal"]
    assert service.get_pending_proposals(workspace)[0]["id"] == proposal["id"]
    assert "idempotency_key" not in service.get_proposal(workspace, proposal["id"])
    assert service.get_proposal(workspace, proposal["id"])["expires_at"] == proposal["expires_at"]
    claimed, ok = repo.claim(
        workspace,
        proposal["id"],
        actor,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    assert ok
    assert service.get_pending_proposals(workspace) == []
    assert (
        service.get_proposal(workspace, proposal["id"])["execution_state"]
        == "in_progress_or_unknown"
    )


def test_operation_history_groups_by_proposal(app):
    application, actions = app
    command = {
        "command_type": "create",
        "task_type": "briefing",
        "parameters": {"interval_days": 1},
        "target_id": None,
        "missing_fields": [],
        "language": "en",
    }
    proposal = actions.propose_action("workspace-1", command)
    result = actions.confirm_action("workspace-1", proposal["id"])
    history = actions.get_action_history("workspace-1")
    grouped = {}
    for event in history:
        grouped.setdefault(event["proposal_id"], []).append(event)
    assert len(grouped) == 1
    assert len(next(iter(grouped.values()))) >= 3
    assert result["status"] == "executed"
    application.parser = Parser({"command_type": "list", "task_type": None, "missing_fields": []})
    summarized = application.handle_text("show action history")
    assert len(summarized["data"]["items"]) == 1
    assert len(summarized["data"]["items"][0]["events"]) == len(history)


def test_unbound_unknown_command_falls_back_to_chat(app):
    application, _ = app
    application.parser = Parser(
        {"command_type": "unknown", "task_type": None, "missing_fields": []}
    )
    assert application.handle_text("hello") == {"handled": False, "reason": "not_proactive"}


def test_explicit_proactive_request_does_not_fall_through_when_context_read_fails(app):
    application, actions = app

    def fail_pending(_workspace):
        raise RuntimeError("storage unavailable")

    actions.get_pending_proposals = fail_pending
    application.parser = Parser(
        {"command_type": "create", "task_type": "briefing", "missing_fields": []}
    )
    result = application.handle_text("schedule a morning briefing every day")
    assert result["handled"] is True
    assert result["error_code"] == "STORAGE_ERROR"


def test_explicit_proactive_request_does_not_fall_through_without_identity(app):
    application, _ = app
    application.actor_provider = lambda: None
    application.parser = Parser(
        {"command_type": "create", "task_type": "briefing", "missing_fields": []}
    )
    result = application.handle_text("schedule a morning briefing")
    assert result["handled"] is True
    assert result["error_code"] == "UNAUTHORIZED"


def test_page_facing_read_apis_return_json_safe_results(app):
    application, _ = app
    for result in (
        application.list_briefings(),
        application.list_watches(),
        application.list_pending_actions(),
        application.list_action_history(),
    ):
        assert result["ok"] is True
        json.dumps(result)


def test_composed_real_briefing_adapter_runs_only_after_confirm(tmp_path):
    class Client:
        response = {
            "command_type": "create",
            "task_type": "briefing",
            "target_id": None,
            "language": "en",
            "parameters": {
                "interval_days": 1,
                "local_time": "08:00",
                "timezone": "Asia/Singapore",
                "start_date": "2026-11-08",
                "enabled": True,
                "order_id": None,
                "condition_type": None,
                "deadline_at": None,
                "expected_status": None,
                "check_interval_minutes": None,
            },
        }

        def generate_structured(self, **_kwargs):
            return self.response

    client = Client()
    application = build_proactive_application(
        llm_client=client,
        actor_provider=lambda: "operator",
        workspace_provider=lambda: "workspace",
        authorizer=lambda *_args: True,
        database=tmp_path / "composed.sqlite3",
    )
    proposal_result = application.handle_text(
        "schedule a briefing every day at 8:00 am starting 2026-11-08"
    )
    assert proposal_result["status"] == "pending_confirmation"
    assert application.list_briefings()["data"]["items"] == []

    client.response = {
        "command_type": "confirm",
        "task_type": None,
        "target_id": proposal_result["id"],
        "language": "en",
        "parameters": {
            key: None
            for key in (
                "interval_days",
                "local_time",
                "timezone",
                "start_date",
                "enabled",
                "order_id",
                "condition_type",
                "deadline_at",
                "expected_status",
                "check_interval_minutes",
            )
        },
    }
    confirmation = application.handle_text("yes")
    assert confirmation["status"] == "executed"
    assert len(application.list_briefings()["data"]["items"]) == 1
