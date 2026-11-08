"""SQLite persistence for scheduled morning briefings."""

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.agent.proactive.briefing.models import BriefingRun, BriefingSchedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefing_schedules (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    interval_days INTEGER NOT NULL CHECK (interval_days > 0),
    local_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    start_date TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancelled_at TEXT,
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_briefing_schedules_due
ON briefing_schedules(enabled, next_run_at);

CREATE INDEX IF NOT EXISTS idx_briefing_schedules_workspace
ON briefing_schedules(workspace_id);

CREATE TABLE IF NOT EXISTS briefing_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    result_json TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY (schedule_id)
        REFERENCES briefing_schedules(id),
    UNIQUE (schedule_id, scheduled_for)
);

CREATE INDEX IF NOT EXISTS idx_briefing_runs_workspace
ON briefing_runs(workspace_id, schedule_id);

CREATE TABLE IF NOT EXISTS briefing_claims (
    schedule_id TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (schedule_id, scheduled_for),
    FOREIGN KEY (schedule_id)
        REFERENCES briefing_schedules(id)
);

CREATE TABLE IF NOT EXISTS briefing_action_receipts (
    workspace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, idempotency_key)
);

"""


class SQLiteBriefingRepository:
    """Persistent SQLite repository for local development."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10.0,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(briefing_schedules)"
                ).fetchall()
            }

            if "cancelled_at" not in columns:
                connection.execute(
                    "ALTER TABLE briefing_schedules "
                    "ADD COLUMN cancelled_at TEXT"
                )

            if "config_version" not in columns:
                connection.execute(
                    "ALTER TABLE briefing_schedules "
                    "ADD COLUMN config_version INTEGER NOT NULL DEFAULT 1 "
                    "CHECK (config_version >= 1)"
                )


    def execute_confirmed_action(
        self,
        workspace_id: str,
        command: dict,
        *,
        idempotency_key: str,
        expected_version: str | None,
    ) -> dict:
        """Execute a confirmed briefing create with durable idempotency."""
        import hashlib
        from uuid import uuid4

        from app.agent.proactive.briefing.scheduler import calculate_next_run
        from app.agent.proactive.briefing.service import BriefingService

        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("INVALID_ARGUMENT")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("INVALID_ARGUMENT")
        if not isinstance(command, dict):
            raise ValueError("INVALID_ARGUMENT")

        if command.get("task_type") != "briefing":
            raise ValueError("INVALID_ARGUMENT")

        operation = command.get("command_type")
        if operation not in {"create", "update", "cancel"}:
            raise ValueError("INVALID_ARGUMENT")

        target_id = command.get("target_id")

        if operation == "create":
            if target_id is not None or expected_version is not None:
                raise ValueError("INVALID_ARGUMENT")
        else:
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError("MISSING_FIELD")
            if (
                not isinstance(expected_version, str)
                or not expected_version.isdecimal()
                or int(expected_version) < 1
            ):
                raise ValueError("INVALID_ARGUMENT")

        parameters = command.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("INVALID_ARGUMENT")

        allowed = {
            "interval_days", "local_time", "timezone",
            "start_date", "enabled",
        }
        if set(parameters) - allowed:
            raise ValueError("INVALID_ARGUMENT")

        if operation == "create":
            values = BriefingService._validate_parameters(parameters)
        elif operation == "update":
            if not parameters:
                raise ValueError("MISSING_FIELD")
            values = None
        else:
            if parameters:
                raise ValueError("INVALID_ARGUMENT")
            values = None

        command_json = json.dumps(
            {
                "command": command,
                "expected_version": expected_version,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        command_hash = hashlib.sha256(
            command_json.encode("utf-8")
        ).hexdigest()

        now = datetime.now(__import__("datetime").timezone.utc)

        next_run = (
            calculate_next_run(
                values["start_date"],
                values["local_time"],
                values["timezone"],
                values["interval_days"],
                now,
            )
            if values is not None and values["enabled"]
            else None
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT command_hash, receipt_json
                FROM briefing_action_receipts
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (workspace_id, idempotency_key),
            ).fetchone()

            if existing is not None:
                if existing[0] != command_hash:
                    raise ValueError("CONFLICT")
                return json.loads(existing[1])


            if operation in {"update", "cancel"}:
                connection.row_factory = sqlite3.Row

                row = connection.execute(
                    """
                    SELECT * FROM briefing_schedules
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (target_id, workspace_id),
                ).fetchone()

                if row is None:
                    raise ValueError("NOT_FOUND")

                if row["cancelled_at"] is not None:
                    raise ValueError("CONFLICT")

                if row["config_version"] != int(expected_version):
                    raise ValueError("CONFLICT")

                if operation == "update":
                    merged = {
                        "interval_days": row["interval_days"],
                        "local_time": time.fromisoformat(
                            row["local_time"]
                        ).strftime("%H:%M"),
                        "timezone": row["timezone"],
                        "start_date": row["start_date"],
                        "enabled": bool(row["enabled"]),
                    }
                    merged.update(parameters)

                    values = BriefingService._validate_parameters(merged)

                    next_run = (
                        calculate_next_run(
                            values["start_date"],
                            values["local_time"],
                            values["timezone"],
                            values["interval_days"],
                            now,
                        )
                        if values["enabled"]
                        else None
                    )

                    cursor = connection.execute(
                        """
                        UPDATE briefing_schedules
                        SET interval_days = ?,
                            local_time = ?,
                            timezone = ?,
                            start_date = ?,
                            enabled = ?,
                            next_run_at = ?,
                            updated_at = ?,
                            config_version = config_version + 1
                        WHERE id = ? AND workspace_id = ?
                          AND cancelled_at IS NULL
                          AND config_version = ?
                        """,
                        (
                            values["interval_days"],
                            values["local_time"].isoformat(),
                            values["timezone"],
                            values["start_date"].isoformat(),
                            int(values["enabled"]),
                            next_run.isoformat() if next_run else None,
                            now.isoformat(),
                            target_id,
                            workspace_id,
                            int(expected_version),
                        ),
                    )

                    status = "updated"

                else:
                    cursor = connection.execute(
                        """
                        UPDATE briefing_schedules
                        SET enabled = 0,
                            next_run_at = NULL,
                            cancelled_at = ?,
                            updated_at = ?,
                            config_version = config_version + 1
                        WHERE id = ? AND workspace_id = ?
                          AND cancelled_at IS NULL
                          AND config_version = ?
                        """,
                        (
                            now.isoformat(),
                            now.isoformat(),
                            target_id,
                            workspace_id,
                            int(expected_version),
                        ),
                    )

                    status = "cancelled"
                    next_run = None

                if cursor.rowcount != 1:
                    raise ValueError("CONFLICT")

                connection.execute(
                    "DELETE FROM briefing_claims WHERE schedule_id = ?",
                    (target_id,),
                )

                receipt = {
                    "ok": True,
                    "status": status,
                    "id": target_id,
                    "data": {
                        "schedule_id": target_id,
                        "config_version": int(expected_version) + 1,
                        "next_run_at": (
                            next_run.isoformat() if next_run else None
                        ),
                    },
                    "error_code": None,
                }

                connection.execute(
                    """
                    INSERT INTO briefing_action_receipts (
                        workspace_id, idempotency_key,
                        command_hash, receipt_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        idempotency_key,
                        command_hash,
                        json.dumps(receipt, ensure_ascii=False),
                        now.isoformat(),
                    ),
                )

                return receipt

            schedule_id = str(uuid4())

            connection.execute(
                """
                INSERT INTO briefing_schedules (
                    id, workspace_id, interval_days, local_time,
                    timezone, start_date, enabled, next_run_at,
                    created_at, updated_at, cancelled_at, config_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    workspace_id,
                    values["interval_days"],
                    values["local_time"].isoformat(),
                    values["timezone"],
                    values["start_date"].isoformat(),
                    int(values["enabled"]),
                    next_run.isoformat() if next_run else None,
                    now.isoformat(),
                    now.isoformat(),
                    None,
                    1,
                ),
            )

            receipt = {
                "ok": True,
                "status": "created",
                "id": schedule_id,
                "data": {
                    "schedule_id": schedule_id,
                    "config_version": 1,
                    "next_run_at": (
                        next_run.isoformat() if next_run else None
                    ),
                },
                "error_code": None,
            }

            connection.execute(
                """
                INSERT INTO briefing_action_receipts (
                    workspace_id, idempotency_key,
                    command_hash, receipt_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    idempotency_key,
                    command_hash,
                    json.dumps(receipt, ensure_ascii=False),
                    now.isoformat(),
                ),
            )

            return receipt

    def create_schedule(self, schedule: BriefingSchedule) -> None:
        """Insert a new schedule into SQLite."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO briefing_schedules (
                    id, workspace_id, interval_days, local_time,
                    timezone, start_date, enabled, next_run_at,
                    created_at, updated_at, cancelled_at, config_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.id,
                    schedule.workspace_id,
                    schedule.interval_days,
                    schedule.local_time.isoformat(),
                    schedule.timezone,
                    schedule.start_date.isoformat(),
                    int(schedule.enabled),
                    (
                        schedule.next_run_at.isoformat()
                        if schedule.next_run_at is not None
                        else None
                    ),
                    schedule.created_at.isoformat(),
                    schedule.updated_at.isoformat(),
                    (
                        schedule.cancelled_at.isoformat()
                        if schedule.cancelled_at is not None
                        else None
                    ),
                    schedule.config_version,
                ),
            )

    def get_schedule(
        self, workspace_id: str, schedule_id: str
    ) -> BriefingSchedule | None:
        """Load a schedule belonging to the requested workspace."""
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT * FROM briefing_schedules
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, schedule_id),
            ).fetchone()

        if row is None:
            return None

        return BriefingSchedule(
            id=row["id"],
            workspace_id=row["workspace_id"],
            interval_days=row["interval_days"],
            local_time=time.fromisoformat(row["local_time"]),
            timezone=row["timezone"],
            start_date=date.fromisoformat(row["start_date"]),
            enabled=bool(row["enabled"]),
            next_run_at=(
                datetime.fromisoformat(row["next_run_at"])
                if row["next_run_at"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            cancelled_at=(
                datetime.fromisoformat(row["cancelled_at"])
                if row["cancelled_at"] is not None
                else None
            ),
            config_version=row["config_version"],
        )

    def update_schedule(self, schedule: BriefingSchedule) -> None:
        """Update an existing schedule within its workspace."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE briefing_schedules
                SET interval_days = ?,
                    local_time = ?,
                    timezone = ?,
                    start_date = ?,
                    enabled = ?,
                    next_run_at = ?,
                    updated_at = ?,
                    cancelled_at = ?,
                    config_version = config_version + 1
                WHERE id = ? AND workspace_id = ?
                  AND cancelled_at IS NULL
                  AND config_version = ?
                """,
                (
                    schedule.interval_days,
                    schedule.local_time.isoformat(),
                    schedule.timezone,
                    schedule.start_date.isoformat(),
                    int(schedule.enabled),
                    (
                        schedule.next_run_at.isoformat()
                        if schedule.next_run_at is not None
                        else None
                    ),
                    schedule.updated_at.isoformat(),
                    (
                        schedule.cancelled_at.isoformat()
                        if schedule.cancelled_at is not None
                        else None
                    ),
                    schedule.id,
                    schedule.workspace_id,
                    schedule.config_version,
                ),
            )

            if cursor.rowcount == 0:
                existing = connection.execute(
                    """
                    SELECT cancelled_at
                    FROM briefing_schedules
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (schedule.id, schedule.workspace_id),
                ).fetchone()

                if existing is not None and existing[0] is not None:
                    raise ValueError("Schedule already cancelled")

                if existing is not None:
                    raise ValueError("Schedule version conflict")

                raise ValueError("Schedule not found")

            # Invalidate old claims within the same transaction.
            connection.execute(
                """
                DELETE FROM briefing_claims
                WHERE schedule_id = ?
                """,
                (schedule.id,),
            )

    def list_schedules(self, workspace_id: str) -> list[BriefingSchedule]:
        """Return all schedules belonging to one workspace."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM briefing_schedules
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (workspace_id,),
            ).fetchall()

        schedules = [
            self.get_schedule(workspace_id, row[0])
            for row in rows
        ]
        return [schedule for schedule in schedules if schedule is not None]

    def list_due_schedules(self, now: datetime) -> list[BriefingSchedule]:
        """Return enabled schedules due at or before the given UTC time."""
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT workspace_id, id
                FROM briefing_schedules
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                """
            ).fetchall()

        schedules = []
        for workspace_id, schedule_id in rows:
            schedule = self.get_schedule(workspace_id, schedule_id)
            if (
                schedule is not None
                and schedule.next_run_at is not None
                and schedule.next_run_at <= now
            ):
                schedules.append(schedule)

        return sorted(
            schedules,
            key=lambda schedule: (schedule.next_run_at, schedule.id),
        )

    def claim_due_schedule(
        self, schedule_id: str, scheduled_for: datetime, now: datetime
    ) -> str | None:
        """Atomically claim one due occurrence with a five-minute lease."""
        from uuid import uuid4

        if (
            now.tzinfo is None
            or now.utcoffset() != timedelta(0)
            or scheduled_for.tzinfo is None
            or scheduled_for.utcoffset() != timedelta(0)
        ):
            raise ValueError("Timestamps must be timezone-aware UTC")

        token = str(uuid4())
        expires_at = now + timedelta(minutes=5)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                """
                SELECT enabled, next_run_at
                FROM briefing_schedules
                WHERE id = ?
                """,
                (schedule_id,),
            ).fetchone()

            if row is None or not row[0] or row[1] is None:
                return None

            next_run_at = datetime.fromisoformat(row[1])
            if next_run_at != scheduled_for or scheduled_for > now:
                return None

            existing_run = connection.execute(
                """
                SELECT 1 FROM briefing_runs
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (schedule_id, scheduled_for.isoformat()),
            ).fetchone()

            if existing_run is not None:
                return None

            existing_claim = connection.execute(
                """
                SELECT expires_at FROM briefing_claims
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (schedule_id, scheduled_for.isoformat()),
            ).fetchone()

            if existing_claim is not None:
                old_expiry = datetime.fromisoformat(existing_claim[0])
                if now < old_expiry:
                    return None

            connection.execute(
                """
                INSERT INTO briefing_claims (
                    schedule_id, scheduled_for, claim_token, expires_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(schedule_id, scheduled_for)
                DO UPDATE SET
                    claim_token = excluded.claim_token,
                    expires_at = excluded.expires_at
                """,
                (
                    schedule_id,
                    scheduled_for.isoformat(),
                    token,
                    expires_at.isoformat(),
                ),
            )

        return token

    def save_run(
        self, run: BriefingRun, claim_token: str, now: datetime
    ) -> None:
        """Atomically save a run using a valid claim token."""
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")

        scheduled_for = run.scheduled_for.isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            schedule = connection.execute(
                """
                SELECT workspace_id, enabled, next_run_at
                FROM briefing_schedules
                WHERE id = ?
                """,
                (run.schedule_id,),
            ).fetchone()

            if schedule is None:
                raise ValueError("Schedule not found")

            if schedule[0] != run.workspace_id:
                raise ValueError("Run workspace does not match schedule workspace")

            existing_run = connection.execute(
                """
                SELECT 1 FROM briefing_runs
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (run.schedule_id, scheduled_for),
            ).fetchone()

            if existing_run is not None:
                raise ValueError("Run already exists")

            claim = connection.execute(
                """
                SELECT claim_token, expires_at
                FROM briefing_claims
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (run.schedule_id, scheduled_for),
            ).fetchone()

            if claim is None:
                raise ValueError("No active claim")

            if claim[0] != claim_token or now >= datetime.fromisoformat(claim[1]):
                raise ValueError("Invalid or expired claim token")

            if (
                not schedule[1]
                or schedule[2] is None
                or datetime.fromisoformat(schedule[2]) != run.scheduled_for
            ):
                raise ValueError("Schedule is no longer eligible")

            connection.execute(
                """
                INSERT INTO briefing_runs (
                    id, workspace_id, schedule_id, scheduled_for,
                    executed_at, status, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.schedule_id,
                    scheduled_for,
                    run.executed_at.isoformat(),
                    run.status,
                    json.dumps(run.result, ensure_ascii=False),
                    run.error,
                ),
            )

            connection.execute(
                """
                DELETE FROM briefing_claims
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (run.schedule_id, scheduled_for),
            )

    def complete_run(
        self,
        run: BriefingRun,
        claim_token: str,
        now: datetime,
        next_run_at: datetime | None,
    ) -> None:
        """Atomically save a run and advance its schedule in SQLite."""
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")

        if next_run_at is not None:
            if (
                next_run_at.tzinfo is None
                or next_run_at.utcoffset() != timedelta(0)
                or next_run_at <= run.scheduled_for
            ):
                raise ValueError("Invalid next_run_at")

        scheduled_for = run.scheduled_for.isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            schedule = connection.execute(
                """
                SELECT workspace_id, enabled, next_run_at, cancelled_at
                FROM briefing_schedules
                WHERE id = ?
                """,
                (run.schedule_id,),
            ).fetchone()

            if schedule is None:
                raise ValueError("Schedule not found")

            if schedule[0] != run.workspace_id:
                raise ValueError("Run workspace does not match schedule workspace")

            if (
                not schedule[1]
                or schedule[3] is not None
                or schedule[2] is None
                or datetime.fromisoformat(schedule[2]) != run.scheduled_for
            ):
                raise ValueError("Schedule is no longer eligible")

            existing_run = connection.execute(
                """
                SELECT 1 FROM briefing_runs
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (run.schedule_id, scheduled_for),
            ).fetchone()

            if existing_run is not None:
                raise ValueError("Run already exists")

            claim = connection.execute(
                """
                SELECT claim_token, expires_at
                FROM briefing_claims
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (run.schedule_id, scheduled_for),
            ).fetchone()

            if claim is None:
                raise ValueError("No active claim")

            if claim[0] != claim_token or now >= datetime.fromisoformat(claim[1]):
                raise ValueError("Invalid or expired claim token")

            connection.execute(
                """
                INSERT INTO briefing_runs (
                    id, workspace_id, schedule_id, scheduled_for,
                    executed_at, status, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.schedule_id,
                    scheduled_for,
                    run.executed_at.isoformat(),
                    run.status,
                    json.dumps(run.result, ensure_ascii=False),
                    run.error,
                ),
            )

            cursor = connection.execute(
                """
                UPDATE briefing_schedules
                SET next_run_at = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                  AND enabled = 1 AND cancelled_at IS NULL
                  AND next_run_at = ?
                """,
                (
                    next_run_at.isoformat() if next_run_at is not None else None,
                    now.isoformat(),
                    run.schedule_id,
                    run.workspace_id,
                    scheduled_for,
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError("Schedule changed during completion")

            connection.execute(
                """
                DELETE FROM briefing_claims
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (run.schedule_id, scheduled_for),
            )

    def list_history(
        self, workspace_id: str, schedule_id: str | None = None
    ) -> list[BriefingRun]:
        """Load briefing execution history scoped to one workspace."""
        query = """
            SELECT r.*
            FROM briefing_runs AS r
            JOIN briefing_schedules AS s
              ON r.schedule_id = s.id
            WHERE r.workspace_id = ?
              AND s.workspace_id = ?
        """
        parameters = [workspace_id, workspace_id]

        if schedule_id is not None:
            query += " AND r.schedule_id = ?"
            parameters.append(schedule_id)

        query += " ORDER BY r.executed_at DESC, r.id DESC"

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()

        return [
            BriefingRun(
                id=row["id"],
                workspace_id=row["workspace_id"],
                schedule_id=row["schedule_id"],
                scheduled_for=datetime.fromisoformat(row["scheduled_for"]),
                executed_at=datetime.fromisoformat(row["executed_at"]),
                status=row["status"],
                result=json.loads(row["result_json"]),
                error=row["error"],
            )
            for row in rows
        ]
