"""Exercise DB-API transaction and parameter boundaries without a live server."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.accounts.identity.models import UserRecord
from app.accounts.identity.postgres import PostgresAttemptRepository, PostgresUserRepository
from app.accounts.identity.repository import DuplicateUsername

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.connection.queries.append((sql, params))
        if self.connection.failure:
            raise self.connection.failure

    def fetchone(self):
        return self.connection.row


class FakeConnection:
    def __init__(self, row=None, failure=None):
        self.row = row
        self.failure = failure
        self.queries = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def user():
    return UserRecord(str(uuid4()), "alice", "alice", "$argon2id$fake", NOW)


def test_user_create_uses_parameters_and_commits():
    db = FakeConnection()
    record = user()
    PostgresUserRepository(lambda: db).create(record)
    sql, params = db.queries[0]
    assert "INSERT INTO account_users" in sql
    assert "alice" not in sql
    assert params[2] == "alice"
    assert db.commits == 1 and db.rollbacks == 0 and db.closed


def test_user_lookup_returns_record_and_closes_connection():
    record = user()
    db = FakeConnection(
        row=(record.user_id, record.username, record.username_key, record.password_hash, NOW)
    )
    assert PostgresUserRepository(lambda: db).get_by_username_key("alice") == record
    assert db.queries[0][1] == ("alice",)
    assert db.closed


def test_unique_username_conflict_rolls_back():
    class UniqueError(Exception):
        sqlstate = "23505"
        diag = type("Diag", (), {"constraint_name": "account_users_username_key_key"})()

    db = FakeConnection(failure=UniqueError())
    with pytest.raises(DuplicateUsername):
        PostgresUserRepository(lambda: db).create(user())
    assert db.rollbacks == 1 and db.commits == 0 and db.closed


def test_other_database_error_is_not_a_duplicate_username():
    db = FakeConnection(failure=OSError("db down"))
    with pytest.raises(OSError):
        PostgresUserRepository(lambda: db).create(user())
    assert db.rollbacks == 1 and db.closed


def test_attempt_count_is_one_atomic_upsert_and_clear_is_parameterized():
    write = FakeConnection(row=(4,))
    clear = FakeConnection()
    connections = iter((write, clear))
    repo = PostgresAttemptRepository(lambda: next(connections))
    assert repo.consume("opaque-hmac", NOW, timedelta(minutes=15)) == 4
    sql, params = write.queries[0]
    assert "ON CONFLICT (attempt_key) DO UPDATE" in sql
    assert "RETURNING attempt_count" in sql
    assert "opaque-hmac" not in sql
    assert params[0] == "opaque-hmac"
    assert write.commits == 1 and write.closed
    repo.clear("opaque-hmac")
    assert clear.queries[0][1] == ("opaque-hmac",)
    assert clear.commits == 1 and clear.closed


def test_attempt_store_failure_rolls_back():
    db = FakeConnection(failure=OSError("db down"))
    with pytest.raises(OSError):
        PostgresAttemptRepository(lambda: db).consume("opaque-hmac", NOW, timedelta(minutes=15))
    assert db.rollbacks == 1 and db.closed
