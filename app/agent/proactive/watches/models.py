"""Local P5 representations of contract concepts, not shared wire schemas.

No lifecycle status, scheduler fields or confirmation fields are introduced.
Record outcomes are internal rule results; see README before integration.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from app.agent.contracts import Evidence
from app.tools.order_status.config import ALLOWED_STATUSES

CONDITIONS = frozenset({"no_progress_by_deadline", "status_equals"})


class WatchValidationError(ValueError):
    """Validation failure with a contract error code; no ServiceResult change."""

    def __init__(self, message: str, error_code: str = "INVALID_ARGUMENT") -> None:
        super().__init__(message)
        self.error_code = error_code


def require_utc(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise WatchValidationError(f"{name} must be a timezone-aware UTC datetime")


def require_identifier(value: str, name: str, *, uuid: bool = False) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WatchValidationError(f"{name} is required")
    if uuid:
        try:
            UUID(value)
        except ValueError as exc:
            raise WatchValidationError(f"{name} must be a UUID string") from exc


class CheckOutcome(str, Enum):
    """Internal outcomes, not Watch lifecycle or shared TaskStatus values."""

    NOT_DUE = "not_due"
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Watch:
    id: str
    workspace_id: str
    order_id: str
    condition_type: str
    check_interval_minutes: int
    deadline_at: datetime | None = None
    expected_status: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.id, "id", uuid=True)
        require_identifier(self.workspace_id, "workspace_id")
        require_identifier(self.order_id, "order_id")
        if self.order_id != self.order_id.strip().upper():
            raise WatchValidationError("order_id must be normalized before constructing Watch")
        if not isinstance(self.condition_type, str) or self.condition_type not in CONDITIONS:
            raise WatchValidationError("Unsupported condition", "UNSUPPORTED_CONDITION")
        interval = self.check_interval_minutes
        if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
            raise WatchValidationError("check_interval_minutes must be a positive integer")
        if self.condition_type == "no_progress_by_deadline":
            if self.deadline_at is None:
                raise WatchValidationError("deadline_at is required", "MISSING_FIELD")
            require_utc(self.deadline_at, "deadline_at")
            if self.expected_status is not None:
                raise WatchValidationError("expected_status is not used by deadline checks")
        else:
            if self.expected_status is None:
                raise WatchValidationError("expected_status is required", "MISSING_FIELD")
            if (
                not isinstance(self.expected_status, str)
                or self.expected_status not in ALLOWED_STATUSES
            ):
                raise WatchValidationError("expected_status must be IN_PROGRESS or COMPLETE")
            if self.deadline_at is not None:
                raise WatchValidationError("Combining status_equals with a deadline is undefined")


def _validate_observation(value: Any) -> None:
    """Accept finite JSON values only, so observations can later be persisted."""
    import json

    def visit(item: Any) -> None:
        if item is None or type(item) in {str, bool, int, float}:
            return
        if isinstance(item, list):
            for element in item:
                visit(element)
            return
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            for element in item.values():
                visit(element)
            return
        raise WatchValidationError("observed_value must contain only JSON values")

    visit(value)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WatchValidationError("observed_value must contain finite JSON values") from exc


@dataclass(frozen=True)
class CheckRecord:
    id: str
    workspace_id: str
    watch_id: str
    timestamp: datetime
    observed_value: dict[str, Any]
    evidence: tuple[Evidence, ...]
    outcome: CheckOutcome

    def __post_init__(self) -> None:
        require_identifier(self.id, "id", uuid=True)
        require_identifier(self.workspace_id, "workspace_id")
        require_identifier(self.watch_id, "watch_id", uuid=True)
        require_utc(self.timestamp, "timestamp")
        if not isinstance(self.outcome, CheckOutcome):
            raise WatchValidationError("outcome must be a CheckOutcome")
        if not isinstance(self.observed_value, dict):
            raise WatchValidationError("observed_value must be a dictionary")
        _validate_observation(self.observed_value)
        if not isinstance(self.evidence, (list, tuple)) or not all(
            isinstance(e, Evidence)
            and all(isinstance(v, str) and v.strip() for v in (e.source, e.reference, e.detail))
            for e in self.evidence
        ):
            raise WatchValidationError("evidence must contain valid shared Evidence objects")
        if self.outcome in {CheckOutcome.MATCHED, CheckOutcome.NOT_MATCHED} and not self.evidence:
            raise WatchValidationError("A business conclusion requires source evidence")
        object.__setattr__(self, "observed_value", deepcopy(self.observed_value))
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class Alert(CheckRecord):
    """A matched observation; no notification delivery or persistence implied."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.outcome != CheckOutcome.MATCHED:
            raise WatchValidationError("An Alert requires a matched condition")
