"""Tests for shared PostgreSQL connection configuration."""

import pytest

from app.accounts.storage.database import create_connection_factory


def test_missing_database_url_is_rejected(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_connection_factory()


def test_invalid_database_url_is_rejected():
    with pytest.raises(ValueError, match="PostgreSQL"):
        create_connection_factory("sqlite:///local.db")


def test_valid_url_returns_factory_without_connecting():
    factory = create_connection_factory(
        "postgresql://example:password@localhost:5432/testdb"
    )

    assert callable(factory)
