"""Cross-module contract checks; run when P4 is present in the integration tree."""

from datetime import datetime, timezone

import pytest

actions = pytest.importorskip("app.agent.proactive.actions")

from app.agent.llm_client import MockLLMClient  # noqa: E402
from app.agent.proactive.understanding import ProactiveCommandParser  # noqa: E402


class FakeBriefingExecutor:
    def __init__(self):
        self.calls = 0

    def validate(self, workspace_id, command):
        assert workspace_id == "demo"
        assert command["parameters"]["interval_days"] == 3
        return None

    def execute(self, workspace_id, command, *, idempotency_key, expected_version):
        self.calls += 1
        assert idempotency_key
        assert expected_version is None
        return {
            "ok": True,
            "status": "created",
            "id": "a7a315e5-e622-4090-aafb-87e8ca08dc06",
            "data": {},
            "error_code": None,
            "message": "simulated",
        }


def _parser_response(command, kind=None, target=None, **parameters):
    return {
        "command_type": command,
        "task_type": kind,
        "target_id": target,
        "language": "en",
        "parameters": parameters,
    }


@pytest.fixture
def p4():
    executor = FakeBriefingExecutor()
    with actions.SQLiteActionRepository(":memory:") as repository:
        service = actions.ActionService(
            repository,
            {("briefing", "create"): executor},
            actor_provider=lambda: "operator",
            authorizer=lambda actor, workspace, operation, proposal: (
                actor == "operator" and workspace == "demo"
            ),
            clock=lambda: datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        yield service, executor


def _create_command():
    response = _parser_response(
        "create",
        "briefing",
        interval_days=3,
        local_time="08:00",
        start_date="2026-11-06",
    )
    return ProactiveCommandParser(MockLLMClient([response])).parse(
        "Schedule a briefing every 3 days from 2026-11-06 at 08:00"
    )


def test_p1_command_proposes_then_p4_confirms_once(p4):
    service, executor = p4
    command = _create_command()
    assert command.missing_fields == []
    proposed = service.propose_action("demo", command.to_dict())
    assert proposed["status"] == "pending_confirmation"
    assert executor.calls == 0
    proposal_id = proposed["id"]
    context = {"proposals": [{"id": proposal_id}]}
    confirmation = ProactiveCommandParser(
        MockLLMClient([_parser_response("confirm", target=proposal_id)])
    ).parse("Yes", context)
    assert confirmation.target_id == proposal_id
    assert service.confirm_action("demo", confirmation.target_id)["status"] == "executed"
    assert service.confirm_action("demo", confirmation.target_id)["data"]["already_executed"]
    assert executor.calls == 1


def test_missing_fields_cannot_be_proposed(p4):
    service, executor = p4
    response = _parser_response("create", "briefing", interval_days=3)
    command = ProactiveCommandParser(MockLLMClient([response])).parse(
        "Schedule a briefing every 3 days"
    )
    assert command.missing_fields == ["local_time", "start_date"]
    result = service.propose_action("demo", command.to_dict())
    assert result["error_code"] == "MISSING_FIELD"
    assert executor.calls == 0


def test_rejection_never_invokes_executor(p4):
    service, executor = p4
    proposal_id = service.propose_action("demo", _create_command().to_dict())["id"]
    rejection = ProactiveCommandParser(
        MockLLMClient([_parser_response("reject", target=proposal_id)])
    ).parse("No, reject it", {"proposals": [{"id": proposal_id}]})
    assert rejection.target_id == proposal_id
    assert service.reject_action("demo", rejection.target_id)["status"] == "rejected"
    assert executor.calls == 0
