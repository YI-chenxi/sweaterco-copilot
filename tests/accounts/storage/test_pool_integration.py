"""Integration tests for pooled PostgreSQL connections.

Run with:
ACCOUNT_POOL_TEST_DSN='postgresql:///sweaterco_test?host=/tmp' \
python -m pytest -q tests/accounts/storage/test_pool_integration.py
"""

import pytest
from psycopg_pool import PoolTimeout

from app.accounts.storage.database import (
    _close_pools,
    create_connection_factory,
)


@pytest.fixture
def factory(monkeypatch):
    import os

    dsn = os.getenv("ACCOUNT_POOL_TEST_DSN")
    if not dsn:
        pytest.skip("ACCOUNT_POOL_TEST_DSN not configured")

    monkeypatch.setenv("ACCOUNT_DB_POOL_ENABLED", "1")

    connection_factory = create_connection_factory(dsn)

    yield connection_factory

    _close_pools()


def test_connection_reuse(factory):
    first = factory()
    try:
        with first.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            first_pid = cursor.fetchone()[0]
    finally:
        first.close()

    second = factory()
    try:
        with second.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            second_pid = cursor.fetchone()[0]
    finally:
        second.close()

    assert first_pid == second_pid


def test_uncommitted_transaction_is_rolled_back(factory):
    first = factory()
    try:
        with first.cursor() as cursor:
            cursor.execute("SET application_name = 'pool_uncommitted_test'")
        # Intentionally do not commit.
    finally:
        first.close()

    second = factory()
    try:
        with second.cursor() as cursor:
            cursor.execute("SHOW application_name")
            name = cursor.fetchone()[0]
    finally:
        second.close()

    assert name != "pool_uncommitted_test"


def test_committed_session_state_is_cleared(factory):
    first = factory()
    try:
        with first.cursor() as cursor:
            cursor.execute("SET application_name = 'pool_committed_test'")
        first.commit()
    finally:
        first.close()

    second = factory()
    try:
        with second.cursor() as cursor:
            cursor.execute("SHOW application_name")
            name = cursor.fetchone()[0]
    finally:
        second.close()

    assert name != "pool_committed_test"


def test_pool_exhaustion_and_recovery(factory):
    first = factory()
    second = factory()
    third = None

    try:
        with pytest.raises(PoolTimeout):
            third = factory()

        first.close()
        first = None

        third = factory()
        with third.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1
    finally:
        for connection in (first, second, third):
            if connection is not None:
                connection.close()


def test_closed_pool_rejects_old_factory(factory):
    from psycopg_pool import PoolClosed

    connection = factory()
    connection.close()

    _close_pools()

    with pytest.raises(PoolClosed):
        factory()
