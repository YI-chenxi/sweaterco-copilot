"""P5-only storage boundary; no changes to shared contracts or wire fields."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.agent.proactive.watches.evaluation import EvaluationBatch, WatchRuntime
from app.agent.proactive.watches.models import Alert, CheckRecord, Watch, WatchValidationError


class RepositoryError(RuntimeError):
    """Storage failure; callers must not represent this as an empty list."""


class RepositoryConflict(RepositoryError):
    """An identifier or idempotency binding conflicts with stored data."""


def validate_workspace(workspace_id: str) -> None:
    if (
        not isinstance(workspace_id, str)
        or not workspace_id.strip()
        or workspace_id != workspace_id.strip()
    ):
        raise WatchValidationError("workspace_id must be a nonempty, unpadded string")


def validate_action_id(action_id: str) -> None:
    if not isinstance(action_id, str) or not action_id.strip() or action_id != action_id.strip():
        raise WatchValidationError("A trusted confirmed-action ID is required for execution")


def validate_version(version: str) -> None:
    if (
        not isinstance(version, str)
        or not version.isascii()
        or not version.isdigit()
        or len(version) > 18
        or int(version) < 1
        or str(int(version)) != version
    ):
        raise WatchValidationError("expected_version must be a canonical positive revision")


@dataclass(frozen=True)
class CancellationContext:
    """P5-local execution metadata, not a public command or proof of permission."""

    idempotency_key: str
    expected_version: str

    def __post_init__(self) -> None:
        validate_action_id(self.idempotency_key)
        validate_version(self.expected_version)


@dataclass(frozen=True)
class WatchState:
    """Internal state view, excluded from public Watch/ServiceResult schemas."""

    watch: Watch
    version: str
    cancelled: bool


class WatchRepository(Protocol):
    """Module-local API. Reads are detached; history is append-only.

    create_watch must atomically bind (workspace, action ID) and insert or replay.
    cancel_watch must retain the binding and existing history.
    """

    def create_watch(self, watch: Watch, action_id: str) -> tuple[Watch, bool]: ...

    def get_watch(self, workspace_id: str, watch_id: str) -> Watch | None: ...

    def cancel_watch(self, workspace_id: str, watch_id: str) -> Watch | None: ...

    def list_watches(self, workspace_id: str) -> list[Watch]: ...

    def save_check(self, record: CheckRecord) -> None: ...

    def save_alert(self, alert: Alert) -> None: ...

    def list_checks(self, workspace_id: str) -> list[CheckRecord]: ...

    def list_alerts(self, workspace_id: str) -> list[Alert]: ...

    def get_watch_state(self, workspace_id: str, watch_id: str) -> WatchState | None: ...

    def get_receipt(self, workspace_id: str, action_id: str) -> dict | None: ...

    def cancel_watch_if_version(
        self,
        workspace_id: str,
        watch_id: str,
        expected_version: str,
        action_id: str,
    ) -> Watch | None: ...

    def get_runtime(self, workspace_id: str, watch_id: str) -> WatchRuntime | None: ...

    def list_due_ids(self, workspace_id: str, now: datetime) -> list[str]: ...

    def process_due(
        self,
        workspace_id: str,
        watch_id: str,
        now: datetime,
        evaluate: Callable[[Watch, WatchRuntime], EvaluationBatch],
    ) -> dict | None: ...
