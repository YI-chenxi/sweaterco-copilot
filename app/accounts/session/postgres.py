"""PostgreSQL DB-API adapter for session metadata and hashed bearer tokens."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from app.accounts.session.models import Session

ConnectionFactory = Callable[[], Any]


class PostgresSessionRepository:
    """PostgreSQL repository using an injected psycopg-compatible connection factory.

    The adapter deliberately imports no database driver and owns no credentials.
    Pass a factory that returns a DB-API connection configured by deployment.
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def create(self, session: Session, token_hash: str) -> None:
        self._write(
            """INSERT INTO account_sessions
               (session_id, user_id, workspace_id, created_at, expires_at,
                last_seen_at, revoked_at, token_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                session.session_id,
                session.user_id,
                session.workspace_id,
                session.created_at,
                session.expires_at,
                session.last_seen_at,
                session.revoked_at,
                token_hash,
            ),
        )

    def get_by_token_hash(self, token_hash: str) -> Session | None:
        row = self._fetch_one(
            "SELECT * FROM account_sessions WHERE token_hash = %s",
            (token_hash,),
        )
        return self._to_session(row) if row else None

    def list_for_owner(self, user_id: str, workspace_id: str) -> list[Session]:
        rows = self._fetch_all(
            """SELECT * FROM account_sessions
               WHERE user_id = %s AND workspace_id = %s
               ORDER BY created_at DESC""",
            (user_id, workspace_id),
        )
        return [self._to_session(row) for row in rows]

    def touch(self, session_id: str, last_seen_at: datetime, expires_at: datetime) -> Session:
        row = self._fetch_one(
            """UPDATE account_sessions SET last_seen_at = %s, expires_at = %s
               WHERE session_id = %s AND revoked_at IS NULL AND expires_at > %s
               RETURNING *""",
            (last_seen_at, expires_at, session_id, last_seen_at),
        )
        if row is None:
            row = self._fetch_one(
                "SELECT * FROM account_sessions WHERE session_id = %s", (session_id,)
            )
        if row is None:
            raise KeyError("session not found")
        return self._to_session(row)

    def revoke(self, session_id: str, revoked_at: datetime) -> Session | None:
        row = self._fetch_one(
            """UPDATE account_sessions SET revoked_at = COALESCE(revoked_at, %s)
               WHERE session_id = %s RETURNING *""",
            (revoked_at, session_id),
        )
        return self._to_session(row) if row else None

    def revoke_owned(
        self, session_id: str, user_id: str, workspace_id: str, revoked_at: datetime
    ) -> Session | None:
        row = self._fetch_one(
            """UPDATE account_sessions SET revoked_at = COALESCE(revoked_at, %s)
               WHERE session_id = %s AND user_id = %s AND workspace_id = %s
               RETURNING *""",
            (revoked_at, session_id, user_id, workspace_id),
        )
        return self._to_session(row) if row else None

    def revoke_user_sessions(self, user_id: str, revoked_at: datetime) -> int:
        return self._write(
            """UPDATE account_sessions SET revoked_at = %s
               WHERE user_id = %s AND revoked_at IS NULL""",
            (revoked_at, user_id),
        )

    def _fetch_one(self, query: str, parameters: Sequence[Any]) -> dict[str, Any] | None:
        return self._query(query, parameters, fetch="one")

    def _fetch_all(self, query: str, parameters: Sequence[Any]) -> list[dict[str, Any]]:
        return self._query(query, parameters, fetch="all")

    def _write(self, query: str, parameters: Sequence[Any]) -> int:
        return self._query(query, parameters, fetch="rowcount")

    def _query(self, query: str, parameters: Sequence[Any], *, fetch: str) -> Any:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(query, parameters)
                if fetch == "one":
                    row = cursor.fetchone()
                    result = self._row_dict(cursor, row) if row is not None else None
                    connection.commit()
                    return result
                if fetch == "all":
                    result = [self._row_dict(cursor, row) for row in cursor.fetchall()]
                    connection.commit()
                    return result
                result = cursor.rowcount
                connection.commit()
                return result
            finally:
                cursor.close()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        names = [
            column.name if hasattr(column, "name") else column[0]
            for column in cursor.description
        ]
        return dict(zip(names, row, strict=True))

    @staticmethod
    def _to_session(row: dict[str, Any]) -> Session:
        return Session(
            session_id=str(row["session_id"]),
            user_id=str(row["user_id"]),
            workspace_id=str(row["workspace_id"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )
