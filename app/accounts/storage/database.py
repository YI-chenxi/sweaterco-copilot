"""Shared PostgreSQL connection configuration for account storage."""

import atexit
import logging
import os
import time
from collections.abc import Callable
from threading import RLock
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from app.accounts.storage.pooled_connection import PooledConnection

_logger = logging.getLogger(__name__)
_pools = {}
_pool_lock = RLock()


def _close_pools():
    with _pool_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


atexit.register(_close_pools)


def create_connection_factory(
    database_url: str | None = None,
) -> Callable[[], Any]:
    """Create a direct or pooled connection factory."""

    url = database_url or os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError("DATABASE_URL is required")

    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL")

    if os.getenv("ACCOUNT_DB_POOL_ENABLED") != "1":

        def connect():
            start = time.perf_counter()
            try:
                return psycopg.connect(url, connect_timeout=10)
            finally:
                if os.getenv("ACCOUNT_DB_PERF_LOG") == "1":
                    _logger.warning(
                        "account_db_connection_ms=%.1f",
                        (time.perf_counter() - start) * 1000,
                    )

        return connect

    # One shared pool per connection string, within this process.
    with _pool_lock:
        pool = _pools.get(url)
        if pool is None:
            pool = ConnectionPool(
                conninfo=url,
                min_size=0,
                max_size=2,
                timeout=5,
                open=False,
            )
            pool.open()
            _pools[url] = pool

    def pooled_connect():
        start = time.perf_counter()
        connection = pool.getconn()
        if os.getenv("ACCOUNT_DB_PERF_LOG") == "1":
            _logger.warning(
                "account_db_pool_acquire_ms=%.1f",
                (time.perf_counter() - start) * 1000,
            )
        return PooledConnection(pool, connection)

    return pooled_connect
