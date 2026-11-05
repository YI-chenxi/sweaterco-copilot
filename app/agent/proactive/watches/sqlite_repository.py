"""P5 durable transactions and conditional mutations, independent of P4 tables."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from app.agent.proactive.watches.evaluation import (
    WatchRuntime,
    check_result,
    load_runtime,
    runtime_data,
    utc_text,
    validate_batch,
)
from app.agent.proactive.watches.models import (
    Alert,
    CheckRecord,
    Watch,
    WatchValidationError,
    require_utc,
)
from app.agent.proactive.watches.repository import (
    RepositoryConflict,
    RepositoryError,
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
    load_record,
    load_watch,
    mutation_receipt,
    normalize_watch,
    record_data,
    uuid_string,
    watch_data,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS p5_object_ids (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('watch','check','alert')),
 UNIQUE(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS p5_watches (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, document TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>=1),
 cancelled INTEGER NOT NULL CHECK(cancelled IN (0,1)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,id) REFERENCES p5_object_ids(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS p5_checks (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, watch_id TEXT NOT NULL,
 timestamp TEXT NOT NULL, document TEXT NOT NULL,
 FOREIGN KEY(workspace_id,id) REFERENCES p5_object_ids(workspace_id,id),
 FOREIGN KEY(workspace_id,watch_id) REFERENCES p5_watches(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS p5_alerts (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, watch_id TEXT NOT NULL,
 timestamp TEXT NOT NULL, document TEXT NOT NULL,
 FOREIGN KEY(workspace_id,id) REFERENCES p5_object_ids(workspace_id,id),
 FOREIGN KEY(workspace_id,watch_id) REFERENCES p5_watches(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS p5_execution_receipts (
 workspace_id TEXT NOT NULL, execution_key TEXT NOT NULL,
 operation TEXT NOT NULL CHECK(operation IN ('create','cancel')),
 watch_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 receipt TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(workspace_id,execution_key),
 FOREIGN KEY(workspace_id,watch_id) REFERENCES p5_watches(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS p5_watch_runtime (
 workspace_id TEXT NOT NULL, watch_id TEXT NOT NULL,
 next_check_at TEXT NOT NULL, document TEXT NOT NULL,
 PRIMARY KEY(workspace_id,watch_id),
 FOREIGN KEY(workspace_id,watch_id) REFERENCES p5_watches(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS p5_watch_runs (
 workspace_id TEXT NOT NULL, watch_id TEXT NOT NULL, scheduled_at TEXT NOT NULL,
 check_id TEXT NOT NULL UNIQUE, alert_id TEXT UNIQUE, receipt TEXT NOT NULL,
 PRIMARY KEY(workspace_id,watch_id,scheduled_at),
 FOREIGN KEY(workspace_id,watch_id) REFERENCES p5_watches(workspace_id,id),
 FOREIGN KEY(workspace_id,check_id) REFERENCES p5_checks(workspace_id,id),
 FOREIGN KEY(workspace_id,alert_id) REFERENCES p5_alerts(workspace_id,id)
);
CREATE INDEX IF NOT EXISTS p5_due_workspace ON p5_watch_runtime(workspace_id,next_check_at);
CREATE UNIQUE INDEX IF NOT EXISTS p5_check_workspace_id ON p5_checks(workspace_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS p5_alert_workspace_id ON p5_alerts(workspace_id,id);
CREATE INDEX IF NOT EXISTS p5_watch_workspace ON p5_watches(workspace_id);
CREATE INDEX IF NOT EXISTS p5_check_workspace ON p5_checks(workspace_id);
CREATE INDEX IF NOT EXISTS p5_alert_workspace ON p5_alerts(workspace_id);
"""


class SQLiteWatchRepository(WatchRepository):
    def __init__(
        self,
        database: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._connection = sqlite3.connect(
            str(database),
            isolation_level=None,
            check_same_thread=False,
            timeout=10,
        )
        try:
            for statement in (
                "PRAGMA foreign_keys=ON",
                "PRAGMA busy_timeout=10000",
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=FULL",
            ):
                self._connection.execute(statement)
            self._connection.executescript(_SCHEMA)
            # Additive Phase 3 -> Phase 4 backfill, serialized with other writers.
            with self._transaction():
                rows = self._connection.execute(
                    "SELECT w.workspace_id,w.id,w.created_at,"
                    "EXISTS(SELECT 1 FROM p5_alerts a WHERE a.workspace_id=w.workspace_id "
                    "AND a.watch_id=w.id) FROM p5_watches w "
                    "LEFT JOIN p5_watch_runtime r ON r.workspace_id=w.workspace_id "
                    "AND r.watch_id=w.id WHERE r.watch_id IS NULL",
                ).fetchall()
                for workspace, watch_id, stamp, latched in rows:
                    anchor = datetime.fromisoformat(stamp)
                    runtime = WatchRuntime(anchor, anchor, alert_latched=bool(latched))
                    self._insert_runtime(workspace, watch_id, runtime)
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _stamp(self) -> str:
        now = self._clock()
        require_utc(now, "repository clock")
        return now.isoformat(timespec="microseconds")

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.execute("COMMIT")
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def _identity(self, record_id: str, workspace: str, kind: str) -> None:
        if self._connection.execute(
            "SELECT 1 FROM p5_object_ids WHERE id=?",
            (record_id,),
        ).fetchone():
            raise RepositoryConflict("Identifier already exists")
        self._connection.execute(
            "INSERT INTO p5_object_ids(id,workspace_id,kind) VALUES(?,?,?)",
            (record_id, workspace, kind),
        )

    def _replay(self, workspace: str, key: str, fingerprint: str) -> dict | None:
        row = self._connection.execute(
            "SELECT fingerprint,receipt FROM p5_execution_receipts "
            "WHERE workspace_id=? AND execution_key=?",
            (workspace, key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise RepositoryConflict("Execution key reused with different operation parameters")
        return json.loads(row[1])

    def _save_receipt(self, workspace, key, operation, watch, fingerprint, stamp):
        self._connection.execute(
            "INSERT INTO p5_execution_receipts "
            "(workspace_id,execution_key,operation,watch_id,fingerprint,receipt,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                workspace,
                key,
                operation,
                watch.id,
                fingerprint,
                dumps(mutation_receipt(watch, operation)),
                stamp,
            ),
        )

    def get_receipt(self, workspace_id: str, action_id: str) -> dict | None:
        validate_workspace(workspace_id)
        validate_action_id(action_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT receipt FROM p5_execution_receipts "
                "WHERE workspace_id=? AND execution_key=?",
                (workspace_id, action_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def create_watch(self, watch: Watch, action_id: str) -> tuple[Watch, bool]:
        if not isinstance(watch, Watch):
            raise WatchValidationError("watch must be a Watch")
        watch = normalize_watch(watch)
        validate_workspace(watch.workspace_id)
        validate_action_id(action_id)
        fingerprint = create_fingerprint(watch)
        with self._transaction():
            receipt = self._replay(watch.workspace_id, action_id, fingerprint)
            if receipt is not None:
                return load_watch(dumps(receipt["data"])), False
            stamp = self._stamp()
            self._identity(watch.id, watch.workspace_id, "watch")
            self._connection.execute(
                "INSERT INTO p5_watches "
                "(id,workspace_id,document,version,cancelled,created_at,updated_at) "
                "VALUES(?,?,?,1,0,?,?)",
                (watch.id, watch.workspace_id, dumps(watch_data(watch)), stamp, stamp),
            )
            anchor = datetime.fromisoformat(stamp)
            self._insert_runtime(watch.workspace_id, watch.id, WatchRuntime(anchor, anchor))
            self._save_receipt(watch.workspace_id, action_id, "create", watch, fingerprint, stamp)
            return watch, True

    def get_watch_state(self, workspace_id: str, watch_id: str) -> WatchState | None:
        validate_workspace(workspace_id)
        watch_id = uuid_string(watch_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT document,version,cancelled FROM p5_watches WHERE workspace_id=? AND id=?",
                (workspace_id, watch_id),
            ).fetchone()
            return WatchState(load_watch(row[0]), str(row[1]), bool(row[2])) if row else None

    def get_watch(self, workspace_id: str, watch_id: str) -> Watch | None:
        state = self.get_watch_state(workspace_id, watch_id)
        return state.watch if state else None

    def cancel_watch(self, workspace_id: str, watch_id: str) -> Watch | None:
        watch_id = uuid_string(watch_id)
        return self._cancel(workspace_id, watch_id, None, legacy_cancel_key(watch_id))

    def cancel_watch_if_version(
        self,
        workspace_id: str,
        watch_id: str,
        expected_version: str,
        action_id: str,
    ) -> Watch | None:
        validate_version(expected_version)
        return self._cancel(workspace_id, uuid_string(watch_id), expected_version, action_id)

    def _cancel(self, workspace, watch_id, expected_version, action_id):
        validate_workspace(workspace)
        validate_action_id(action_id)
        fingerprint = cancel_fingerprint(watch_id, expected_version)
        with self._transaction():
            receipt = self._replay(workspace, action_id, fingerprint)
            if receipt is not None:
                return load_watch(dumps(receipt["data"]))
            state = self.get_watch_state(workspace, watch_id)
            if state is None:
                return None
            if expected_version is not None and (
                state.cancelled or state.version != expected_version
            ):
                raise RepositoryConflict("Watch version or cancellation state changed")
            stamp = self._stamp()
            if not state.cancelled:
                cursor = self._connection.execute(
                    "UPDATE p5_watches SET cancelled=1,version=version+1,updated_at=? "
                    "WHERE workspace_id=? AND id=? AND version=? AND cancelled=0",
                    (stamp, workspace, watch_id, int(state.version)),
                )
                if cursor.rowcount != 1:
                    raise RepositoryConflict("Conditional cancellation failed")
            self._save_receipt(workspace, action_id, "cancel", state.watch, fingerprint, stamp)
            return state.watch

    def list_watches(self, workspace_id: str) -> list[Watch]:
        validate_workspace(workspace_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT document FROM p5_watches "
                "WHERE workspace_id=? AND cancelled=0 ORDER BY rowid",
                (workspace_id,),
            ).fetchall()
            return [load_watch(row[0]) for row in rows]

    def _insert_record(self, record: CheckRecord, *, alert: bool) -> None:
        """Caller holds the transaction; also used by atomic due-check batches."""
        if type(record) is not (Alert if alert else CheckRecord):
            raise WatchValidationError("Incorrect record type")
        record = replace(record, id=uuid_string(record.id), watch_id=uuid_string(record.watch_id))
        validate_workspace(record.workspace_id)
        table = "p5_alerts" if alert else "p5_checks"
        document = dumps(record_data(record))
        state = self.get_watch_state(record.workspace_id, record.watch_id)
        if state is None:
            raise WatchValidationError("Watch not found", "NOT_FOUND")
        existing = self._connection.execute(
            f"SELECT document FROM {table} WHERE workspace_id=? AND id=?",
            (record.workspace_id, record.id),
        ).fetchone()
        if existing is not None:
            if existing[0] != document:
                raise RepositoryConflict("Append-only record cannot be overwritten")
            return
        if state.cancelled:
            raise RepositoryConflict("Cannot append records to a cancelled watch")
        self._identity(record.id, record.workspace_id, "alert" if alert else "check")
        self._connection.execute(
            f"INSERT INTO {table}(id,workspace_id,watch_id,timestamp,document) VALUES(?,?,?,?,?)",
            (record.id, record.workspace_id, record.watch_id, utc_text(record.timestamp), document),
        )

    def _save_record(self, record: CheckRecord, *, alert: bool) -> None:
        with self._transaction():
            self._insert_record(record, alert=alert)

    def save_check(self, record: CheckRecord) -> None:
        self._save_record(record, alert=False)

    def save_alert(self, alert: Alert) -> None:
        self._save_record(alert, alert=True)

    def _list_records(self, workspace, *, alert):
        validate_workspace(workspace)
        table = "p5_alerts" if alert else "p5_checks"
        with self._lock:
            rows = self._connection.execute(
                f"SELECT document FROM {table} WHERE workspace_id=? ORDER BY rowid",
                (workspace,),
            ).fetchall()
            return [load_record(row[0], alert=alert) for row in rows]

    def list_checks(self, workspace_id: str) -> list[CheckRecord]:
        return self._list_records(workspace_id, alert=False)

    def list_alerts(self, workspace_id: str) -> list[Alert]:
        return self._list_records(workspace_id, alert=True)

    def _insert_runtime(self, workspace, watch_id, runtime):
        self._connection.execute(
            "INSERT INTO p5_watch_runtime(workspace_id,watch_id,next_check_at,document) "
            "VALUES(?,?,?,?)",
            (workspace, watch_id, utc_text(runtime.next_check_at), dumps(runtime_data(runtime))),
        )

    def get_runtime(self, workspace_id, watch_id):
        validate_workspace(workspace_id)
        watch_id = uuid_string(watch_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT document FROM p5_watch_runtime WHERE workspace_id=? AND watch_id=?",
                (workspace_id, watch_id),
            ).fetchone()
            return load_runtime(json.loads(row[0])) if row else None

    def list_due_ids(self, workspace_id, now):
        validate_workspace(workspace_id)
        stamp = utc_text(now)
        with self._lock:
            return [
                row[0]
                for row in self._connection.execute(
                    "SELECT w.id FROM p5_watches w JOIN p5_watch_runtime r "
                    "ON r.workspace_id=w.workspace_id AND r.watch_id=w.id "
                    "WHERE w.workspace_id=? AND w.cancelled=0 AND r.next_check_at<=? "
                    "ORDER BY w.rowid",
                    (workspace_id, stamp),
                ).fetchall()
            ]

    def process_due(self, workspace_id, watch_id, now, evaluate):
        utc_text(now)
        validate_workspace(workspace_id)
        watch_id = uuid_string(watch_id)
        with self._transaction():
            state = self.get_watch_state(workspace_id, watch_id)
            if state is None or state.cancelled:
                return None
            runtime = self.get_runtime(workspace_id, watch_id)
            if runtime is None:
                raise RepositoryError("Watch runtime missing")
            if runtime.next_check_at > now:
                return None
            # The local source call is serialized with this occurrence's commit. No external API.
            batch = evaluate(state.watch, runtime)
            validate_batch(state.watch, runtime, batch, now)
            self._insert_record(batch.record, alert=False)
            if batch.alert is not None:
                self._insert_record(batch.alert, alert=True)
            result = check_result(batch.record)
            self._connection.execute(
                "INSERT INTO p5_watch_runs "
                "(workspace_id,watch_id,scheduled_at,check_id,alert_id,receipt) "
                "VALUES(?,?,?,?,?,?)",
                (
                    workspace_id,
                    watch_id,
                    utc_text(runtime.next_check_at),
                    batch.record.id,
                    batch.alert.id if batch.alert else None,
                    dumps(result),
                ),
            )
            self._connection.execute(
                "UPDATE p5_watch_runtime SET next_check_at=?,document=? "
                "WHERE workspace_id=? AND watch_id=?",
                (
                    utc_text(batch.runtime.next_check_at),
                    dumps(runtime_data(batch.runtime)),
                    workspace_id,
                    watch_id,
                ),
            )
            return result
