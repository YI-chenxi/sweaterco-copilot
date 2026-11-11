"""Tests for pooled connection lease lifecycle."""

from unittest.mock import MagicMock, Mock, call

import pytest

from app.accounts.storage.pooled_connection import PooledConnection


def make_connection():
    connection = MagicMock()
    connection.closed = False
    connection.autocommit = False
    return connection


def test_close_returns_connection_once():
    pool = Mock()
    connection = make_connection()
    lease = PooledConnection(pool, connection)

    lease.close()
    lease.close()

    connection.rollback.assert_called_once()
    connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
        "DISCARD ALL"
    )
    pool.putconn.assert_called_once_with(connection)
    assert lease.closed is True


def test_commit_is_forwarded():
    pool = Mock()
    connection = make_connection()
    lease = PooledConnection(pool, connection)

    lease.commit()
    connection.commit.assert_called_once()

    lease.close()
    pool.putconn.assert_called_once_with(connection)


def test_released_connection_cannot_be_reused():
    pool = Mock()
    connection = make_connection()
    lease = PooledConnection(pool, connection)

    lease.close()

    with pytest.raises(RuntimeError):
        lease.commit()


def test_closed_physical_connection_is_returned_for_disposal():
    pool = Mock()
    connection = make_connection()
    connection.closed = True

    lease = PooledConnection(pool, connection)
    lease.close()

    connection.rollback.assert_not_called()
    connection.cursor.assert_not_called()
    pool.putconn.assert_called_once_with(connection)


def test_rollback_failure_closes_physical_connection():
    pool = Mock()
    connection = make_connection()
    connection.rollback.side_effect = RuntimeError("rollback failed")

    lease = PooledConnection(pool, connection)

    with pytest.raises(RuntimeError, match="rollback failed"):
        lease.close()

    connection.close.assert_called_once()
    pool.putconn.assert_called_once_with(connection)


def test_discard_failure_closes_physical_connection():
    pool = Mock()
    connection = make_connection()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("discard failed")

    lease = PooledConnection(pool, connection)

    with pytest.raises(RuntimeError, match="discard failed"):
        lease.close()

    connection.close.assert_called_once()
    pool.putconn.assert_called_once_with(connection)


def test_autocommit_restored_after_cleanup():
    pool = Mock()
    connection = make_connection()
    lease = PooledConnection(pool, connection)

    lease.close()

    assert connection.autocommit is False
    assert connection.mock_calls.count(call.rollback()) == 1
    pool.putconn.assert_called_once_with(connection)
