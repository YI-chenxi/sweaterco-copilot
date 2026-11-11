"""Shared PostgreSQL connection configuration for account storage."""

import os
from collections.abc import Callable
from functools import cache
from threading import Lock
from typing import Any

import psycopg

_pool_lock = Lock()


@cache
def _connection_pool(url: str):
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        make_conninfo(url),
        kwargs={"connect_timeout": 10, "prepare_threshold": None},
        min_size=0,
        max_size=4,
        timeout=12,
        close_returns=True,
        open=True,
    )


def create_connection_factory(
    database_url: str | None = None,
    *,
    reuse: bool = False,
) -> Callable[[], Any]:
    """Return a factory compatible with P1/P2 PostgreSQL repositories."""

    url = database_url or os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError("DATABASE_URL is required")

    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL")

    if reuse:
        # The repositories already close each checkout in a finally block.
        with _pool_lock:
            return _connection_pool(url).getconn

    def connect() -> psycopg.Connection:
        return psycopg.connect(
            url,
            connect_timeout=10,
        )

    return connect
