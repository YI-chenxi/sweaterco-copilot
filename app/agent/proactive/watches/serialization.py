"""P5-only JSON and receipt serialization; no new public fields."""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from app.agent.contracts import Evidence
from app.agent.proactive.watches.models import (
    Alert,
    CheckOutcome,
    CheckRecord,
    Watch,
    require_identifier,
)


def uuid_string(value: str) -> str:
    require_identifier(value, "identifier", uuid=True)
    return str(UUID(value))


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def watch_data(watch: Watch) -> dict:
    data = asdict(watch)
    data["deadline_at"] = watch.deadline_at.isoformat() if watch.deadline_at else None
    return data


def load_watch(document: str) -> Watch:
    data = json.loads(document)
    if data["deadline_at"] is not None:
        data["deadline_at"] = datetime.fromisoformat(data["deadline_at"])
    return Watch(**data)


def record_data(record: CheckRecord) -> dict:
    data = asdict(record)
    data["timestamp"] = record.timestamp.isoformat()
    data["outcome"] = record.outcome.value
    data["evidence"] = [asdict(e) for e in record.evidence]
    return data


def load_record(document: str, *, alert: bool) -> CheckRecord:
    data = json.loads(document)
    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
    data["outcome"] = CheckOutcome(data["outcome"])
    data["evidence"] = tuple(Evidence(**e) for e in data["evidence"])
    return (Alert if alert else CheckRecord)(**data)


def normalize_watch(watch: Watch) -> Watch:
    return replace(watch, id=uuid_string(watch.id))


def fingerprint(operation: str, payload: dict) -> str:
    return hashlib.sha256(dumps({"operation": operation, "payload": payload}).encode()).hexdigest()


def create_fingerprint(watch: Watch) -> str:
    data = watch_data(watch)
    del data["id"]
    return fingerprint("create", data)


def cancel_fingerprint(watch_id: str, expected_version: str | None) -> str:
    return fingerprint("cancel", {"watch_id": watch_id, "expected_version": expected_version})


def legacy_cancel_key(watch_id: str) -> str:
    return f"p5-internal-cancel:{watch_id}"


def mutation_receipt(watch: Watch, operation: str) -> dict:
    status, message = (
        ("created", "Watch created.")
        if operation == "create"
        else ("cancelled", "Watch cancelled.")
    )
    return {
        "ok": True,
        "status": status,
        "id": watch.id,
        "data": watch_data(watch),
        "error_code": None,
        "message": message,
    }
