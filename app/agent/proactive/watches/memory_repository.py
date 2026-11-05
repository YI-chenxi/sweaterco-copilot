"""Thread-safe process-local storage. It is NOT durable across restarts."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID

from app.agent.proactive.watches.evaluation import (
    WatchRuntime,
    check_result,
    utc_text,
    validate_batch,
)
from app.agent.proactive.watches.models import (
    Alert,
    CheckRecord,
    Watch,
    WatchValidationError,
    require_identifier,
    require_utc,
)
from app.agent.proactive.watches.repository import (
    RepositoryConflict,
    WatchRepository,
    WatchState,
    validate_action_id,
    validate_version,
    validate_workspace,
)
from app.agent.proactive.watches.serialization import (
    cancel_fingerprint,
    create_fingerprint,
    dumps,
    legacy_cancel_key,
    mutation_receipt,
    record_data,
)


class InMemoryWatchRepository(WatchRepository):
    """Local tombstones preserve cancellation and idempotency, without new model fields."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._runtime: dict[tuple[str, str], WatchRuntime] = {}
        self._runs: dict[tuple[str, str, str], dict] = {}
        self._lock = RLock()
        self._watches: dict[str, Watch] = {}
        self._cancelled: set[str] = set()
        self._actions: dict[tuple[str, str], str] = {}
        self._receipts: dict[tuple[str, str], dict] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._versions: dict[str, int] = {}
        self._checks: dict[str, CheckRecord] = {}
        self._alerts: dict[str, Alert] = {}

    def _unused_id(self, record_id: str) -> None:
        if record_id in self._watches or record_id in self._checks or record_id in self._alerts:
            raise RepositoryConflict("Identifier already exists")

    def create_watch(self, watch: Watch, action_id: str) -> tuple[Watch, bool]:
        if not isinstance(watch, Watch):
            raise WatchValidationError("watch must be a Watch")
        watch = replace(watch, id=str(UUID(watch.id)))
        validate_workspace(watch.workspace_id)
        validate_action_id(action_id)
        key = (watch.workspace_id, action_id)
        with self._lock:
            fingerprint = create_fingerprint(watch)
            if key in self._receipts:
                if self._fingerprints[key] != fingerprint:
                    raise RepositoryConflict("Execution key reused with different parameters")
                return deepcopy(self._watches[self._actions[key]]), False
            now = self._clock()
            require_utc(now, "repository clock")
            runtime = WatchRuntime(now, now)
            self._unused_id(watch.id)
            self._runtime[(watch.workspace_id, watch.id)] = runtime
            self._watches[watch.id] = deepcopy(watch)
            self._actions[key] = watch.id
            self._versions[watch.id] = 1
            self._fingerprints[key] = fingerprint
            self._receipts[key] = mutation_receipt(watch, "create")
            return deepcopy(watch), True

    def get_watch(self, workspace_id: str, watch_id: str) -> Watch | None:
        validate_workspace(workspace_id)
        require_identifier(watch_id, "watch_id", uuid=True)
        watch_id = str(UUID(watch_id))
        with self._lock:
            watch = self._watches.get(watch_id)
            return deepcopy(watch) if watch and watch.workspace_id == workspace_id else None

    def get_watch_state(self, workspace_id: str, watch_id: str) -> WatchState | None:
        with self._lock:
            watch = self.get_watch(workspace_id, watch_id)
            return (
                WatchState(watch, str(self._versions[watch.id]), watch.id in self._cancelled)
                if watch
                else None
            )

    def get_receipt(self, workspace_id: str, action_id: str) -> dict | None:
        validate_workspace(workspace_id)
        validate_action_id(action_id)
        with self._lock:
            return deepcopy(self._receipts.get((workspace_id, action_id)))

    def cancel_watch(self, workspace_id: str, watch_id: str) -> Watch | None:
        require_identifier(watch_id, "watch_id", uuid=True)
        watch_id = str(UUID(watch_id))
        return self._cancel(workspace_id, watch_id, None, legacy_cancel_key(watch_id))

    def cancel_watch_if_version(
        self,
        workspace_id: str,
        watch_id: str,
        expected_version: str,
        action_id: str,
    ) -> Watch | None:
        require_identifier(watch_id, "watch_id", uuid=True)
        validate_version(expected_version)
        return self._cancel(workspace_id, str(UUID(watch_id)), expected_version, action_id)

    def _cancel(self, workspace_id, watch_id, expected_version, action_id):
        validate_workspace(workspace_id)
        validate_action_id(action_id)
        key = (workspace_id, action_id)
        fingerprint = cancel_fingerprint(watch_id, expected_version)
        with self._lock:
            if key in self._receipts:
                if self._fingerprints[key] != fingerprint:
                    raise RepositoryConflict("Execution key reused with different parameters")
                return deepcopy(self._watches[self._actions[key]])
            state = self.get_watch_state(workspace_id, watch_id)
            if state is None:
                return None
            if expected_version is not None and (
                state.cancelled or state.version != expected_version
            ):
                raise RepositoryConflict("Watch version or cancellation state changed")
            if not state.cancelled:
                self._cancelled.add(watch_id)
                self._versions[watch_id] += 1
            self._actions[key] = watch_id
            self._fingerprints[key] = fingerprint
            self._receipts[key] = mutation_receipt(state.watch, "cancel")
            return state.watch

    def list_watches(self, workspace_id: str) -> list[Watch]:
        validate_workspace(workspace_id)
        with self._lock:
            return deepcopy(
                [
                    watch
                    for watch in self._watches.values()
                    if watch.workspace_id == workspace_id and watch.id not in self._cancelled
                ]
            )

    def _save_record(self, record: CheckRecord, *, alert: bool) -> None:
        expected_type = Alert if alert else CheckRecord
        if type(record) is not expected_type:
            raise WatchValidationError(f"Expected {expected_type.__name__}")
        # Revalidate: nested observed_value can have been modified after construction.
        record = replace(record, id=str(UUID(record.id)), watch_id=str(UUID(record.watch_id)))
        validate_workspace(record.workspace_id)
        with self._lock:
            watch = self.get_watch(record.workspace_id, record.watch_id)
            if watch is None:
                raise WatchValidationError("Watch not found", "NOT_FOUND")
            target = self._alerts if alert else self._checks
            existing = target.get(record.id)
            if existing is not None:
                if dumps(record_data(existing)) != dumps(record_data(record)):
                    raise RepositoryConflict("Append-only record cannot be overwritten")
                return
            if record.watch_id in self._cancelled:
                raise RepositoryConflict("Cannot append records to a cancelled watch")
            self._unused_id(record.id)
            target[record.id] = deepcopy(record)

    def save_check(self, record: CheckRecord) -> None:
        self._save_record(record, alert=False)

    def save_alert(self, alert: Alert) -> None:
        self._save_record(alert, alert=True)

    def list_checks(self, workspace_id: str) -> list[CheckRecord]:
        validate_workspace(workspace_id)
        with self._lock:
            return deepcopy([r for r in self._checks.values() if r.workspace_id == workspace_id])

    def list_alerts(self, workspace_id: str) -> list[Alert]:
        validate_workspace(workspace_id)
        with self._lock:
            return deepcopy([r for r in self._alerts.values() if r.workspace_id == workspace_id])

    def get_runtime(self, workspace_id: str, watch_id: str) -> WatchRuntime | None:
        with self._lock:
            watch = self.get_watch(workspace_id, watch_id)
            return deepcopy(self._runtime.get((workspace_id, watch.id))) if watch else None

    def list_due_ids(self, workspace_id: str, now: datetime) -> list[str]:
        validate_workspace(workspace_id)
        require_utc(now, "now")
        with self._lock:
            return [
                watch.id
                for watch in self.list_watches(workspace_id)
                if self._runtime[(workspace_id, watch.id)].next_check_at <= now
            ]

    def process_due(self, workspace_id, watch_id, now, evaluate) -> dict | None:
        require_utc(now, "now")
        with self._lock:
            state = self.get_watch_state(workspace_id, watch_id)
            if state is None or state.cancelled:
                return None
            key = (workspace_id, state.watch.id)
            runtime = self._runtime[key]
            if runtime.next_check_at > now:
                return None
            run_key = (*key, utc_text(runtime.next_check_at))
            if run_key in self._runs:
                raise RepositoryConflict("Occurrence already processed")
            batch = deepcopy(evaluate(deepcopy(state.watch), deepcopy(runtime)))
            validate_batch(state.watch, runtime, batch, now)
            latest = self.get_watch_state(workspace_id, state.watch.id)
            if latest.cancelled or latest.version != state.version:
                return None
            # Preflight both identifiers before mutating any state.
            self._unused_id(batch.record.id)
            if batch.alert is not None:
                self._unused_id(batch.alert.id)
                if batch.alert.id == batch.record.id:
                    raise RepositoryConflict("Check and alert identifiers must differ")
            result = deepcopy(check_result(batch.record))
            self._checks[batch.record.id] = batch.record
            if batch.alert is not None:
                self._alerts[batch.alert.id] = batch.alert
            self._runtime[key] = batch.runtime
            self._runs[run_key] = result
            return deepcopy(result)
