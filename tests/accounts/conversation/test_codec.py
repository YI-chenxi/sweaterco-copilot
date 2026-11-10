import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from enum import Enum

import pytest

from app.accounts.conversation.codec import (
    decode_reply,
    decode_session,
    document_copy,
    encode_reply,
    encode_session,
)
from app.accounts.conversation.errors import ConversationError
from app.agent.contracts import AgentReply, Evidence
from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    TaskState,
    TaskStatus,
)
from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.turn_service import ChatSession


def sample_session():
    issue = InterpretationIssue(
        InterpretationIssueType.POSSIBLE_TYPO,
        field="pieces",
        original_value="8OO",
        suggested_value=800,
        decision_index=1,
        task_id="task-002",
    )
    tasks = [
        TaskState(
            "task-001",
            IntentDecision(Intent.ORDER_STATUS, {"order_id": "ORD-057"}),
            TaskStatus.COMPLETED,
        ),
        TaskState(
            "task-002",
            IntentDecision(
                Intent.ORDER_FEASIBILITY,
                {"category": "TOPS", "pieces": 800},
                ("requested_delivery_date",),
                ResponseLanguage.CHINESE,
            ),
            TaskStatus.PENDING,
            [issue],
        ),
        TaskState("task-003", IntentDecision(Intent.MORNING_BRIEFING), TaskStatus.READY),
        TaskState("task-004", IntentDecision(Intent.UNKNOWN), TaskStatus.FAILED),
    ]
    return ChatSession(
        ConversationState(tasks, ResponseLanguage.CHINESE), ["task-002", "task-001"], [issue], 5
    )


def sample_reply():
    return AgentReply(
        text="Evidence-backed result",
        evidence=[Evidence("orders.csv", "ORD-057", "raw row")],
        needs_clarification=True,
        suggested_question="Delivery date?",
        warnings=["Old snapshot"],
        assumptions=["Sunday excluded"],
        metadata={
            "raw": {"type": "date", "value": "ordinary user text"},
            "nested": [None, True, 3, 1.5, {"tuple": ("x", 2)}],
            "date": date(2026, 4, 25),
            "datetime": datetime(2026, 4, 25, 3, 4, 5, 6, timezone(timedelta(hours=8)), fold=1),
            "intent": Intent.ORDER_FEASIBILITY,
        },
    )


def test_session_roundtrip_preserves_every_field_and_task_lifecycle():
    session = sample_session()
    document = encode_session(session)
    restored = decode_session(json.dumps(document))
    assert restored == session
    assert [t.status for t in restored.state.tasks] == list(
        [TaskStatus.COMPLETED, TaskStatus.PENDING, TaskStatus.READY, TaskStatus.FAILED]
    )
    assert restored.active_task_ids == ["task-002", "task-001"]
    assert restored.next_task_number == 5
    restored.state.tasks[1].decision.slots["pieces"] = 999
    assert session.state.tasks[1].decision.slots["pieces"] == 800
    assert decode_session(document) == session


def test_reply_roundtrip_preserves_evidence_metadata_and_safe_type_tags():
    reply = sample_reply()
    encoded = encode_reply(reply)
    restored = decode_reply(json.dumps(encoded))
    assert restored == reply
    assert type(restored.metadata["intent"]) is Intent
    assert type(restored.metadata["date"]) is date
    assert restored.metadata["datetime"].fold == 1
    assert type(restored.metadata["nested"][4]["tuple"]) is tuple
    restored.metadata["nested"].append("changed")
    assert decode_reply(encoded) == reply
    assert encode_reply(decode_reply(encoded)) == encoded


@pytest.mark.parametrize("value", [2, 0, -1, "1", True, None])
def test_schema_versions_fail_explicitly(value):
    doc = encode_session(ChatSession())
    doc["schema_version"] = value
    with pytest.raises(ConversationError) as error:
        decode_session(doc)
    assert error.value.code == ("UNSUPPORTED_SCHEMA" if type(value) is int else "INVALID_STATE")


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        "null",
        '{"schema_version":1,"schema_version":1}',
        '{"a": NaN}',
        '{"a": Infinity}',
        {"a": float("nan")},
        {1: "bad"},
        {"a": object()},
    ],
)
def test_malformed_or_non_json_documents_rejected(raw):
    with pytest.raises(ConversationError):
        document_copy(raw)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra="ignored?"),
        lambda d: d.update(kind="agent_reply"),
        lambda d: d["payload"].pop("global_issues"),
        lambda d: d["payload"].update(next_task_number=0),
        lambda d: d["payload"].update(next_task_number=True),
        lambda d: d["payload"].update(active_task_ids=["not-a-task"]),
        lambda d: d["payload"].update(active_task_ids=["task-001", "task-001"]),
        lambda d: d["payload"]["state"]["tasks"].append(
            deepcopy(d["payload"]["state"]["tasks"][0])
        ),
        lambda d: d["payload"]["state"]["tasks"][0].update(status="imaginary"),
        lambda d: d["payload"]["state"]["tasks"][0].update(task_id=""),
        lambda d: d["payload"]["state"].update(language="fr"),
        lambda d: d["payload"]["global_issues"][0].update(decision_index=-1),
        lambda d: d["payload"]["global_issues"][0].update(field=7),
        lambda d: d["payload"]["state"]["tasks"][0]["decision"].update(
            slots={"type": "python", "class": "os.system"}
        ),
        lambda d: d["payload"]["state"]["tasks"][0]["decision"].update(missing_slots=[1]),
    ],
)
def test_invalid_session_is_not_silently_reset(change):
    doc = encode_session(sample_session())
    change(doc)
    with pytest.raises(ConversationError):
        decode_session(doc)


@pytest.mark.parametrize(
    "packed",
    [
        {"type": "enum", "enum": "subprocess", "value": "run"},
        {"type": "enum", "enum": "Intent", "value": "invented"},
        {"type": "mapping", "items": [["x", 1], ["x", 2]]},
        {"type": "mapping", "items": [[2, 1]]},
        {"type": "tuple", "items": 1},
        {"type": "date", "value": "2026-99-99"},
        {"type": "datetime", "value": "bad", "fold": 0},
        {"type": "datetime", "value": "2026-04-25", "fold": 2},
        {"type": "date", "value": "2026-04-25", "extra": 1},
    ],
)
def test_unknown_or_invalid_metadata_tag_rejected(packed):
    doc = encode_reply(AgentReply("ok"))
    doc["payload"]["metadata"] = {"type": "mapping", "items": [["x", packed]]}
    with pytest.raises(ConversationError):
        decode_reply(doc)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["payload"].update(needs_clarification=1),
        lambda d: d["payload"].update(text=None),
        lambda d: d["payload"].update(suggested_question=3),
        lambda d: d["payload"].update(warnings=[False]),
        lambda d: d["payload"].update(assumptions="text"),
        lambda d: d["payload"]["evidence"][0].update(reference=7),
        lambda d: d["payload"]["evidence"][0].update(extra="unexpected"),
        lambda d: d["payload"].update(metadata={"type": "tuple", "items": []}),
    ],
)
def test_invalid_reply_cannot_be_rendered_as_if_valid(change):
    doc = encode_reply(sample_reply())
    change(doc)
    with pytest.raises(ConversationError):
        decode_reply(doc)


class UnknownEnum(Enum):
    UNKNOWN = "unknown"


@pytest.mark.parametrize(
    "value",
    [
        object(),
        {1, 2},
        b"binary",
        float("inf"),
        float("nan"),
        {3: "key"},
        UnknownEnum.UNKNOWN,
    ],
)
def test_unsupported_values_are_not_dropped_or_stringified(value):
    with pytest.raises(ConversationError, match="safely stored") as error:
        encode_reply(AgentReply("ok", metadata={"bad": value}))
    assert error.value.code == "UNSUPPORTED_VALUE"


def test_size_depth_and_cycles_fail_safely():
    with pytest.raises(ConversationError) as error:
        document_copy({"x": "long"}, limit=5)
    assert error.value.code == "PAYLOAD_TOO_LARGE"
    with pytest.raises(ConversationError):
        document_copy('{"x":"long"}', limit=5)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ConversationError):
        encode_reply(AgentReply("ok", metadata={"cycle": cycle}))
    with pytest.raises(ConversationError):
        document_copy({"cycle": cycle})
    with pytest.raises(ConversationError):
        encode_session(None)
    with pytest.raises(ConversationError):
        encode_reply(None)
