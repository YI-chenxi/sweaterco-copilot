"""PostgreSQL DB-API adapters; Person 3 owns the actual schema migration."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .models import UserRecord
from .repository import DuplicateUsername

ConnectionFactory = Callable[[], Any]


def _close(connection: Any) -> None:
    connection.close()


class PostgresUserRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def create(self, user: UserRecord) -> None:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO account_users
                       (user_id, username, username_key, password_hash, created_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        user.user_id,
                        user.username,
                        user.username_key,
                        user.password_hash,
                        user.created_at,
                    ),
                )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
            sqlstate = getattr(exc, "sqlstate", getattr(exc, "pgcode", None))
            if sqlstate == "23505" and constraint == "account_users_username_key_key":
                raise DuplicateUsername() from exc
            raise
        finally:
            _close(connection)

    def get_by_username_key(self, username_key: str) -> UserRecord | None:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT user_id, username, username_key, password_hash, created_at
                       FROM account_users WHERE username_key = %s""",
                    (username_key,),
                )
                row = cursor.fetchone()
            connection.commit()
            if row is None:
                return None
            return UserRecord(str(row[0]), row[1], row[2], row[3], row[4])
        finally:
            _close(connection)


class PostgresAttemptRepository:
    """One atomic upsert counts concurrent login attempts per HMAC key."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def consume(self, key: str, now: datetime, window: timedelta) -> int:
        connection = self.connection_factory()
        try:
            cutoff = now - window
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO account_login_attempts
                       (attempt_key, window_started_at, attempt_count)
                       VALUES (%s, %s, 1)
                       ON CONFLICT (attempt_key) DO UPDATE SET
                       window_started_at = CASE
                         WHEN account_login_attempts.window_started_at <= %s
                         THEN EXCLUDED.window_started_at
                         ELSE account_login_attempts.window_started_at END,
                       attempt_count = CASE
                         WHEN account_login_attempts.window_started_at <= %s
                         THEN 1 ELSE account_login_attempts.attempt_count + 1 END
                       RETURNING attempt_count""",
                    (key, now, cutoff, cutoff),
                )
                row = cursor.fetchone()
            if row is None:
                raise RuntimeError("attempt counter did not return a row")
            connection.commit()
            return int(row[0])
        except Exception:
            connection.rollback()
            raise
        finally:
            _close(connection)

    def clear(self, key: str) -> None:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM account_login_attempts WHERE attempt_key = %s", (key,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            _close(connection)
