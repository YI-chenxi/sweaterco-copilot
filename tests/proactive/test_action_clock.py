from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.agent.llm_client import MockLLMClient
from app.agent.proactive.clock import action_now
from app.agent.proactive.integration import build_proactive_application


def test_action_clock_uses_snapshot_date_and_live_local_time():
    live = datetime(2026, 11, 12, 14, 23, 45, 123456, tzinfo=ZoneInfo("Asia/Singapore"))

    result = action_now(reference_date=date(2026, 4, 1), live_now=live)

    local = result.astimezone(ZoneInfo("Asia/Singapore"))
    assert local.date() == date(2026, 4, 1)
    assert local.timetz().replace(tzinfo=None) == live.timetz().replace(tzinfo=None)


def test_composed_actions_use_the_injected_snapshot_clock(tmp_path):
    now = datetime(2026, 4, 1, 6, 23, 45, tzinfo=timezone.utc)
    application = build_proactive_application(
        llm_client=MockLLMClient([]),
        actor_provider=lambda: "operator",
        workspace_provider=lambda: "workspace",
        authorizer=lambda *_args: True,
        database=tmp_path / "clock.sqlite3",
        action_clock=lambda: now,
    )
    proposal = application.actions.propose_action(
        "workspace",
        {
            "command_type": "create",
            "task_type": "briefing",
            "target_id": None,
            "language": "en",
            "missing_fields": [],
            "parameters": {
                "interval_days": 1,
                "local_time": "08:00",
                "timezone": "Asia/Singapore",
                "start_date": "2026-04-01",
                "enabled": True,
            },
        },
    )

    record = proposal["data"]["proposal"]
    assert record["created_at"].startswith("2026-04-01T06:23:45")
    assert record["expires_at"].startswith("2026-04-01T06:38:45")
