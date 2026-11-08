from datetime import datetime, timezone

import pytest

from app.accounts.session import PostgresSessionRepository, Session

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    description = None
    rowcount = 1

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.query = None
        self.parameters = None

    def execute(self, query, parameters):
        self.query = query
        self.parameters = parameters
        if self.error:
            raise self.error

    def fetchone(self):
        return self.response

    def fetchall(self):
        return self.response or []

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def session() -> Session:
    return Session(
        session_id="f13d3da8-2f28-4bf4-9614-21c64e01dce0",
        user_id="user-1",
        workspace_id="workspace-1",
        created_at=NOW,
        expires_at=NOW,
        last_seen_at=NOW,
    )


def test_create_uses_parameterized_insert_and_commits() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    repository = PostgresSessionRepository(lambda: connection)

    repository.create(session(), "a" * 64)

    assert "INSERT INTO account_sessions" in cursor.query
    assert "%s" in cursor.query
    assert cursor.parameters[-1] == "a" * 64
    assert connection.commits == 1
    assert connection.closed


def test_owner_listing_scopes_by_both_user_and_workspace() -> None:
    cursor = FakeCursor(response=[])
    connection = FakeConnection(cursor)
    repository = PostgresSessionRepository(lambda: connection)

    assert repository.list_for_owner("user-1", "workspace-1") == []
    assert "user_id = %s AND workspace_id = %s" in cursor.query
    assert cursor.parameters == ("user-1", "workspace-1")
    assert connection.commits == 1


def test_id_revocation_is_owner_scoped_and_maps_returned_session() -> None:
    row = {
        "session_id": session().session_id,
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "created_at": NOW,
        "expires_at": NOW,
        "last_seen_at": NOW,
        "revoked_at": NOW,
        "token_hash": "b" * 64,
    }
    cursor = FakeCursor(response=row)
    connection = FakeConnection(cursor)
    repository = PostgresSessionRepository(lambda: connection)

    revoked = repository.revoke_owned(
        session().session_id, "user-1", "workspace-1", NOW
    )

    assert revoked is not None
    assert revoked.revoked_at == NOW
    assert "session_id = %s AND user_id = %s AND workspace_id = %s" in cursor.query
    assert cursor.parameters[1:] == (session().session_id, "user-1", "workspace-1")


def test_database_failure_rolls_back_and_closes_connection() -> None:
    cursor = FakeCursor(error=RuntimeError("db unavailable"))
    connection = FakeConnection(cursor)
    repository = PostgresSessionRepository(lambda: connection)

    with pytest.raises(RuntimeError, match="db unavailable"):
        repository.create(session(), "c" * 64)

    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.closed
