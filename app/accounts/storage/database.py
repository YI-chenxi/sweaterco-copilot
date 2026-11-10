"""Shared PostgreSQL connection configuration for account storage."""

import os
from collections.abc import Callable
from typing import Any

import psycopg


def create_connection_factory(
    database_url: str | None = None,
) -> Callable[[], Any]:
    """Return a factory compatible with P1/P2 PostgreSQL repositories."""

    url = database_url or os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError("DATABASE_URL is required")

    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL")

    def connect() -> psycopg.Connection:
        return psycopg.connect(
            url,
            connect_timeout=10,
        )

    return connect
