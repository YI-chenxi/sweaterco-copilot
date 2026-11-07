"""Versioned allowlisted codecs for the existing ChatSession and AgentReply.

No pickle, dynamic imports, eval, silent resets or business-tool execution.
"""

import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any

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

from .errors import ConversationError

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 64
_ENUMS = {
    "Intent": Intent,
    "ResponseLanguage": ResponseLanguage,
    "TaskStatus": TaskStatus,
    "InterpretationIssueType": InterpretationIssueType,
}


def _fail(code: str = "INVALID_STATE"):
    raise ConversationError(code)


def _keys(value: Any, fields: set[str]) -> dict:
    if type(value) is not dict or set(value) != fields:
        _fail()
    return value


def _string(value: Any) -> str:
    if type(value) is not str:
        _fail()
    return value


def _strings(value: Any) -> list[str]:
    if type(value) is not list:
        _fail()
    return [_string(item) for item in value]


def _integer(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail()
    return value


def _guard(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        _fail()
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _guard(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _guard(item, depth + 1)
        return
    _fail()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def document_copy(raw: dict | str, *, limit: int = MAX_DOCUMENT_BYTES) -> dict:
    """Strict JSON document copy, including duplicate-key and size checks."""
    try:
        if type(limit) is not int or limit <= 0:
            _fail("INVALID_ARGUMENT")
        if type(raw) is str:
            if len(raw.encode("utf-8")) > limit:
                _fail("PAYLOAD_TOO_LARGE")
            raw = json.loads(raw, object_pairs_hook=_pairs)
        _guard(raw)
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > limit:
            _fail("PAYLOAD_TOO_LARGE")
        result = json.loads(encoded)
        if type(result) is not dict:
            _fail()
        return result
    except ConversationError:
        raise
    except Exception:
        _fail()


def _pack(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        _fail("UNSUPPORTED_VALUE")
    if isinstance(value, Enum):
        for name, enum_type in _ENUMS.items():
            if type(value) is enum_type:
                return {"type": "enum", "enum": name, "value": value.value}
        _fail("UNSUPPORTED_VALUE")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is datetime:
        return {"type": "datetime", "value": value.isoformat(), "fold": value.fold}
    if type(value) is date:
        return {"type": "date", "value": value.isoformat()}
    if type(value) is list:
        return [_pack(item, depth + 1) for item in value]
    if type(value) is tuple:
        return {"type": "tuple", "items": [_pack(item, depth + 1) for item in value]}
    if type(value) is dict and all(type(key) is str for key in value):
        # Encode EVERY mapping, so ordinary user data cannot collide with a type tag.
        return {
            "type": "mapping",
            "items": [[key, _pack(item, depth + 1)] for key, item in value.items()],
        }
    _fail("UNSUPPORTED_VALUE")


def _unpack(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        _fail()
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return [_unpack(item, depth + 1) for item in value]
    if type(value) is not dict:
        _fail()
    kind = value.get("type")
    if kind == "mapping":
        _keys(value, {"type", "items"})
        if type(value["items"]) is not list:
            _fail()
        result = {}
        for pair in value["items"]:
            if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str:
                _fail()
            if pair[0] in result:
                _fail()
            result[pair[0]] = _unpack(pair[1], depth + 1)
        return result
    if kind == "tuple":
        _keys(value, {"type", "items"})
        if type(value["items"]) is not list:
            _fail()
        return tuple(_unpack(item, depth + 1) for item in value["items"])
    if kind == "date":
        _keys(value, {"type", "value"})
        result = date.fromisoformat(_string(value["value"]))
        if result.isoformat() != value["value"]:
            _fail()
        return result
    if kind == "datetime":
        _keys(value, {"type", "value", "fold"})
        fold = _integer(value["fold"])
        if fold not in (0, 1):
            _fail()
        return datetime.fromisoformat(_string(value["value"])).replace(fold=fold)
    if kind == "enum":
        _keys(value, {"type", "enum", "value"})
        name = _string(value["enum"])
        if name not in _ENUMS:
            _fail()
        return _ENUMS[name](_string(value["value"]))
    _fail()


def _envelope(kind: str, payload: dict) -> dict:
    return document_copy({"schema_version": SCHEMA_VERSION, "kind": kind, "payload": payload})


def _payload(raw: dict | str, kind: str) -> dict:
    data = document_copy(raw)
    _keys(data, {"schema_version", "kind", "payload"})
    if type(data["schema_version"]) is not int:
        _fail()
    if data["schema_version"] != SCHEMA_VERSION:
        _fail("UNSUPPORTED_SCHEMA")
    if data["kind"] != kind or type(data["payload"]) is not dict:
        _fail()
    return data["payload"]


def _issue_to_data(issue: InterpretationIssue) -> dict:
    if not isinstance(issue, InterpretationIssue):
        _fail("UNSUPPORTED_VALUE")
    return {
        "issue_type": issue.issue_type.value,
        "field": issue.field,
        "original_value": _pack(issue.original_value),
        "suggested_value": _pack(issue.suggested_value),
        "decision_index": issue.decision_index,
        "task_id": issue.task_id,
    }


def _issue_from_data(raw: dict) -> InterpretationIssue:
    _keys(
        raw,
        {"issue_type", "field", "original_value", "suggested_value", "decision_index", "task_id"},
    )
    for field in ("field", "task_id"):
        if raw[field] is not None:
            _string(raw[field])
    if raw["decision_index"] is not None:
        _integer(raw["decision_index"])
    return InterpretationIssue(
        issue_type=InterpretationIssueType(_string(raw["issue_type"])),
        field=raw["field"],
        original_value=_unpack(raw["original_value"]),
        suggested_value=_unpack(raw["suggested_value"]),
        decision_index=raw["decision_index"],
        task_id=raw["task_id"],
    )


def _decode_session(raw: dict | str) -> ChatSession:
    body = _payload(raw, "chat_session")
    _keys(body, {"state", "active_task_ids", "global_issues", "next_task_number"})
    state = _keys(body["state"], {"tasks", "language"})
    if type(state["tasks"]) is not list or type(body["global_issues"]) is not list:
        _fail()
    tasks = []
    for raw_task in state["tasks"]:
        task = _keys(raw_task, {"task_id", "decision", "status", "issues"})
        task_id = _string(task["task_id"])
        if not task_id:
            _fail()
        decision = _keys(task["decision"], {"intent", "slots", "missing_slots", "language"})
        slots = _unpack(decision["slots"])
        if type(slots) is not dict or type(task["issues"]) is not list:
            _fail()
        tasks.append(
            TaskState(
                task_id=task_id,
                decision=IntentDecision(
                    Intent(_string(decision["intent"])),
                    slots,
                    tuple(_strings(decision["missing_slots"])),
                    ResponseLanguage(_string(decision["language"])),
                ),
                status=TaskStatus(_string(task["status"])),
                issues=[_issue_from_data(issue) for issue in task["issues"]],
            )
        )
    ids = [task.task_id for task in tasks]
    active = _strings(body["active_task_ids"])
    if len(ids) != len(set(ids)) or len(active) != len(set(active)) or not set(active) <= set(ids):
        _fail()
    return ChatSession(
        state=ConversationState(tasks, ResponseLanguage(_string(state["language"]))),
        active_task_ids=active,
        global_issues=[_issue_from_data(issue) for issue in body["global_issues"]],
        next_task_number=_integer(body["next_task_number"], 1),
    )


def decode_session(raw: dict | str) -> ChatSession:
    """Reconstruct all existing fields without executing or resetting tasks."""
    try:
        return _decode_session(raw)
    except ConversationError:
        raise
    except Exception:
        _fail()


def encode_session(session: ChatSession) -> dict:
    try:
        if not isinstance(session, ChatSession):
            _fail("UNSUPPORTED_VALUE")
        payload = {
            "state": {
                "language": session.state.language.value,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "status": task.status.value,
                        "decision": {
                            "intent": task.decision.intent.value,
                            "slots": _pack(task.decision.slots),
                            "missing_slots": list(task.decision.missing_slots),
                            "language": task.decision.language.value,
                        },
                        "issues": [_issue_to_data(issue) for issue in task.issues],
                    }
                    for task in session.state.tasks
                ],
            },
            "active_task_ids": session.active_task_ids,
            "global_issues": [_issue_to_data(issue) for issue in session.global_issues],
            "next_task_number": session.next_task_number,
        }
        result = _envelope("chat_session", payload)
        decode_session(result)  # Validate graph identity and enums before persisting.
        return result
    except ConversationError:
        raise
    except Exception:
        _fail("UNSUPPORTED_VALUE")


def _decode_reply(raw: dict | str) -> AgentReply:
    body = _payload(raw, "agent_reply")
    _keys(
        body,
        {
            "text",
            "evidence",
            "needs_clarification",
            "suggested_question",
            "warnings",
            "assumptions",
            "metadata",
        },
    )
    if type(body["needs_clarification"]) is not bool or type(body["evidence"]) is not list:
        _fail()
    if body["suggested_question"] is not None:
        _string(body["suggested_question"])
    evidence = []
    for raw_evidence in body["evidence"]:
        item = _keys(raw_evidence, {"source", "reference", "detail"})
        evidence.append(Evidence(**{key: _string(value) for key, value in item.items()}))
    metadata = _unpack(body["metadata"])
    if type(metadata) is not dict:
        _fail()
    return AgentReply(
        text=_string(body["text"]),
        evidence=evidence,
        needs_clarification=body["needs_clarification"],
        suggested_question=body["suggested_question"],
        warnings=_strings(body["warnings"]),
        assumptions=_strings(body["assumptions"]),
        metadata=metadata,
    )


def decode_reply(raw: dict | str) -> AgentReply:
    try:
        return _decode_reply(raw)
    except ConversationError:
        raise
    except Exception:
        _fail()


def encode_reply(reply: AgentReply) -> dict:
    try:
        if not isinstance(reply, AgentReply):
            _fail("UNSUPPORTED_VALUE")
        result = _envelope(
            "agent_reply",
            {
                "text": reply.text,
                "evidence": [
                    {"source": item.source, "reference": item.reference, "detail": item.detail}
                    for item in reply.evidence
                ],
                "needs_clarification": reply.needs_clarification,
                "suggested_question": reply.suggested_question,
                "warnings": reply.warnings,
                "assumptions": reply.assumptions,
                "metadata": _pack(reply.metadata),
            },
        )
        decode_reply(result)
        return result
    except ConversationError:
        raise
    except Exception:
        _fail("UNSUPPORTED_VALUE")
