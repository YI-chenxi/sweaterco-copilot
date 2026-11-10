"""Fixtures for opt-in PostgreSQL integration tests."""

import os

import psycopg
import pytest


@pytest.fixture
def pg_connection_factory():
    """Connect only to the dedicated local SweaterCo test database."""
    database = os.getenv("SWEATERCO_TEST_DB", "sweaterco_test")

    if database != "sweaterco_test":
        pytest.fail("Refusing to use a non-test database")

    def connect():
        connection = psycopg.connect(
            dbname=database,
            host="/tmp",
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != "sweaterco_test":
                connection.close()
                pytest.fail("Unexpected database")
        connection.commit()
        return connection

    try:
        with connect():
            pass
    except psycopg.OperationalError:
        pytest.skip("Local PostgreSQL test database unavailable")

    return connect
