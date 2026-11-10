"""SQLite adapter with durable, atomic claims and append-only audit operations."""

import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .models import ActionError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_proposals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    request_key TEXT,
    fingerprint TEXT NOT NULL,
    document TEXT NOT NULL,
    UNIQUE(workspace_id, creator_id, request_key)
);
CREATE INDEX IF NOT EXISTS action_workspace ON action_proposals(workspace_id);
CREATE TABLE IF NOT EXISTS action_audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL REFERENCES action_proposals(id),
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_workspace ON action_audit(workspace_id, sequence);
"""


class SQLiteActionRepository:
    """One locked connection per instance; SQLite serializes distinct instances.

    Use a file under runtime_data/ for development persistence. :memory: is useful
    for tests only. Do not share a SQLite file between independently deployed hosts.
    """

    def __init__(self, database: str | Path) -> None:
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(database), isolation_level=None, check_same_thread=False, timeout=10
        )
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.executescript(_SCHEMA)
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteActionRepository":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

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

    def _get(self, workspace_id: str, proposal_id: str) -> dict:
        row = self._connection.execute(
            "SELECT document FROM action_proposals WHERE workspace_id = ? AND id = ?",
            (workspace_id, proposal_id),
        ).fetchone()
        if row is None:
            raise ActionError("NOT_FOUND")
        return json.loads(row[0])

    def _save(self, proposal: dict) -> None:
        self._connection.execute(
            "UPDATE action_proposals SET document = ? WHERE workspace_id = ? AND id = ?",
            (json.dumps(proposal, ensure_ascii=False), proposal["workspace_id"], proposal["id"]),
        )

    def _audit(self, proposal: dict, event: str, actor_id: str, at: str) -> None:
        record = {
            "id": str(uuid4()),
            "proposal_id": proposal["id"],
            "workspace_id": proposal["workspace_id"],
            "actor_id": actor_id,
            "event": event,
            "timestamp": at,
            "status": proposal["status"],
            "action_type": proposal["action_type"],
            "target_id": proposal["command"]["target_id"],
            "parameters": proposal["command"]["parameters"],
            "error_code": proposal["error_code"],
            "result": proposal["result"],
        }
        self._connection.execute(
            "INSERT INTO action_audit(id, workspace_id, proposal_id, document) VALUES(?,?,?,?)",
            (
                record["id"],
                record["workspace_id"],
                record["proposal_id"],
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def get(self, workspace_id: str, proposal_id: str) -> dict:
        with self._lock:
            return self._get(workspace_id, proposal_id)

    def list_proposals(self, workspace_id: str) -> list[dict]:
        """Return detached proposals for one workspace in creation order."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT document FROM action_proposals WHERE workspace_id = ? ORDER BY rowid DESC",
                (workspace_id,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def create(self, proposal: dict) -> tuple[dict, bool]:
        proposal = deepcopy(proposal)
        with self._transaction():
            if proposal["request_key"] is not None:
                row = self._connection.execute(
                    "SELECT document FROM action_proposals "
                    "WHERE workspace_id = ? AND creator_id = ? AND request_key = ?",
                    (proposal["workspace_id"], proposal["creator_id"], proposal["request_key"]),
                ).fetchone()
                if row is not None:
                    existing = json.loads(row[0])
                    if existing["fingerprint"] != proposal["fingerprint"]:
                        raise ActionError("CONFLICT")
                    return existing, False
            self._connection.execute(
                "INSERT INTO action_proposals "
                "(id,workspace_id,creator_id,request_key,fingerprint,document) VALUES(?,?,?,?,?,?)",
                (
                    proposal["id"],
                    proposal["workspace_id"],
                    proposal["creator_id"],
                    proposal["request_key"],
                    proposal["fingerprint"],
                    json.dumps(proposal, ensure_ascii=False),
                ),
            )
            self._audit(proposal, "proposed", proposal["creator_id"], proposal["created_at"])
            return proposal, True

    def _expire(self, proposal: dict, actor_id: str, at: str) -> dict:
        proposal.update(status="expired", error_code="EXPIRED", finished_at=at)
        self._save(proposal)
        self._audit(proposal, "expired", actor_id, at)
        return proposal

    def claim(
        self, workspace_id: str, proposal_id: str, actor_id: str, at: str
    ) -> tuple[dict, bool]:
        with self._transaction():
            proposal = self._get(workspace_id, proposal_id)
            if proposal["status"] != "pending_confirmation" or proposal["execution_started_at"]:
                return proposal, False
            if proposal["expires_at"] <= at:
                return self._expire(proposal, actor_id, at), False
            proposal["execution_started_at"] = at
            self._save(proposal)
            self._audit(proposal, "execution_started", actor_id, at)
            return proposal, True

    def finish(
        self,
        workspace_id: str,
        proposal_id: str,
        actor_id: str,
        at: str,
        *,
        status: str,
        result: dict | None,
        error_code: str | None,
    ) -> dict:
        if status not in {"executed", "failed", "expired"}:
            raise ActionError("INVALID_ARGUMENT")
        with self._transaction():
            proposal = self._get(workspace_id, proposal_id)
            if proposal["status"] != "pending_confirmation" or not proposal["execution_started_at"]:
                raise ActionError("CONFLICT")
            proposal.update(
                status=status, result=deepcopy(result), error_code=error_code, finished_at=at
            )
            self._save(proposal)
            self._audit(proposal, status, actor_id, at)
            return proposal

    def reject(self, workspace_id: str, proposal_id: str, actor_id: str, at: str) -> dict:
        with self._transaction():
            proposal = self._get(workspace_id, proposal_id)
            if proposal["status"] != "pending_confirmation":
                return proposal
            if proposal["execution_started_at"]:
                raise ActionError("CONFLICT")
            if proposal["expires_at"] <= at:
                return self._expire(proposal, actor_id, at)
            proposal.update(status="rejected", finished_at=at)
            self._save(proposal)
            self._audit(proposal, "rejected", actor_id, at)
            return proposal

    def history(self, workspace_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT document FROM action_audit WHERE workspace_id = ? ORDER BY sequence",
                (workspace_id,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]
