"""P1 contract and safety checks with offline model outputs."""

from app.agent.llm_client import LLMClientError, MockLLMClient
from app.agent.proactive.understanding import ProactiveCommandParser

SCHEDULE_ID = "57e26e90-343a-426c-b0ed-50784144e095"
PROPOSAL_ID = "0e92a447-6808-4cd0-bcf6-319824796258"
OTHER_PROPOSAL_ID = "edb27dfc-dad1-4851-93d5-27015bc4c726"


def response(command="unknown", kind=None, target=None, **parameters):
    return {
        "command_type": command,
        "task_type": kind,
        "target_id": target,
        "language": "en",
        "parameters": parameters,
    }


def parse(text, model_response, context=None):
    client = MockLLMClient([model_response])
    return ProactiveCommandParser(client).parse(text, context)


def test_bilingual_briefing_creation_and_normalization():
    result = parse(
        "从2026年11月6日开始每3天早上8点生成晨报",
        response(
            "create", "briefing", interval_days=3, local_time="08:00", start_date="2026-11-06"
        ),
    )
    assert result.command_type == "create"
    assert result.task_type == "briefing"
    assert result.language == "zh"
    assert result.parameters == {
        "interval_days": 3,
        "local_time": "08:00",
        "start_date": "2026-11-06",
        "timezone": "Asia/Singapore",
    }
    assert result.missing_fields == []
    assert result.to_dict()["reference_text"] is None


def test_missing_schedule_values_are_not_invented():
    result = parse(
        "Schedule a morning briefing every day",
        response(
            "create", "briefing", interval_days=1, local_time="08:00", start_date="2026-11-06"
        ),
    )
    assert result.parameters == {"interval_days": 1, "timezone": "Asia/Singapore"}
    assert result.missing_fields == ["local_time", "start_date"]


def test_watch_requires_exact_time_and_retains_order_id():
    result = parse(
        "Tell me if ORD058 has not moved by Thursday",
        response(
            "create",
            "watch",
            order_id="ORD-058",
            condition_type="no_progress_by_deadline",
            deadline_at="2026-11-05T18:00:00+08:00",
            check_interval_minutes=60,
        ),
    )
    assert result.parameters["order_id"] == "ORD-058"
    assert "deadline_at" not in result.parameters
    assert result.missing_fields == ["deadline_at"]


def test_watch_accepts_spoken_order_number_without_confirmation():
    result = parse(
        "Watch order 002 for Complete, checking every 60 minutes",
        response(
            "create",
            "watch",
            order_id="order 002",
            condition_type="status_equals",
            expected_status="COMPLETE",
            check_interval_minutes=60,
        ),
    )
    assert result.parameters["order_id"] == "ORD-002"
    assert result.missing_fields == []


def test_watch_explicit_utc_deadline():
    result = parse(
        "Watch ORD-058 for no progress by 2026-11-06 18:00, check every hour",
        response(
            "create",
            "watch",
            order_id="ORD058",
            condition_type="no_progress_by_deadline",
            deadline_at="2026-11-06T18:00:00+08:00",
            check_interval_minutes=60,
        ),
    )
    assert result.parameters["deadline_at"] == "2026-11-06T10:00:00+00:00"
    assert result.missing_fields == []


def test_invalid_order_typo_is_not_silently_corrected():
    result = parse(
        "Watch ODR058 for status SHIPPED",
        response(
            "create",
            "watch",
            order_id="ORD-058",
            condition_type="status_equals",
            expected_status="SHIPPED",
            check_interval_minutes=60,
        ),
    )
    assert "order_id" not in result.parameters
    assert "order_id" in result.missing_fields


def test_confirm_must_bind_to_known_unique_proposal():
    context = {"proposals": [{"id": PROPOSAL_ID}, {"id": OTHER_PROPOSAL_ID}]}
    result = parse("Yes", response("confirm", target=PROPOSAL_ID), context)
    assert result.target_id is None
    assert result.missing_fields == ["target_id"]
    result = parse(f"Confirm {PROPOSAL_ID}", response("confirm", target=PROPOSAL_ID), context)
    assert result.target_id == PROPOSAL_ID


def test_unknown_or_unsupported_commands_cannot_become_actions():
    assert parse("Send a chase-up message", response("unknown")).command_type == "unknown"
    assert parse("Update this watch", response("update", "watch")).command_type == "unknown"
    assert parse("Why is this order at risk?", response("unknown")).command_type == "unknown"
    assert parse("Give me a briefing now", response("create", "briefing")).command_type == "unknown"
    assert (
        parse("What is the status of ORD058?", response("create", "watch")).command_type
        == "unknown"
    )


def test_targeted_update_cancel_and_read_only_list():
    context = {"briefings": [{"id": SCHEDULE_ID}]}
    updated = parse(
        f"Change {SCHEDULE_ID} to 19:30",
        response("update", "briefing", SCHEDULE_ID, local_time="19:30"),
        context,
    )
    assert updated.target_id == SCHEDULE_ID
    assert updated.parameters == {"local_time": "19:30"}
    assert updated.missing_fields == []
    cancelled = parse(f"Cancel {SCHEDULE_ID}", response("cancel", "briefing", SCHEDULE_ID), context)
    assert cancelled.target_id == SCHEDULE_ID
    assert cancelled.parameters == {}
    listed = parse("List my watches", response("list", "watch"))
    assert listed.command_type == "list"
    assert listed.target_id is None


def test_rejection_cannot_be_misread_as_confirmation():
    context = {"proposals": [{"id": PROPOSAL_ID}]}
    result = parse(f"Don't confirm {PROPOSAL_ID}", response("confirm", target=PROPOSAL_ID), context)
    assert result.command_type == "unknown"
    result = parse(f"Reject {PROPOSAL_ID}", response("reject", target=PROPOSAL_ID), context)
    assert result.command_type == "reject"
    assert result.target_id == PROPOSAL_ID


def test_non_uuid_target_is_not_forwarded_to_p4():
    context = {"proposals": [{"id": "proposal-1"}]}
    result = parse("Confirm proposal-1", response("confirm", target="proposal-1"), context)
    assert result.target_id is None
    assert result.missing_fields == ["target_id"]


def test_bad_model_output_fails_closed():
    client = MockLLMClient([LLMClientError("offline")])
    result = ProactiveCommandParser(client).parse("取消提醒")
    assert result.command_type == "unknown"
    assert result.language == "zh"
    assert result.parameters == {}
