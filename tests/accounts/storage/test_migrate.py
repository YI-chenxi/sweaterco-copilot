"""Tests for migration discovery and validation."""

from pathlib import Path

import pytest

from app.accounts.storage.migrate import discover_migrations


def test_migrations_are_sorted(tmp_path: Path):
    (tmp_path / "002_second.sql").write_text("SELECT 2;")
    (tmp_path / "001_first.sql").write_text("SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert [path.name for path in migrations] == [
        "001_first.sql",
        "002_second.sql",
    ]


def test_duplicate_versions_rejected(tmp_path: Path):
    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    (tmp_path / "001_other.sql").write_text("SELECT 2;")

    with pytest.raises(ValueError, match="Duplicate migration version"):
        discover_migrations(tmp_path)


def test_non_migration_files_are_ignored(tmp_path: Path):
    (tmp_path / "README.md").write_text("notes")
    (tmp_path / "001_first.sql").write_text("SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert len(migrations) == 1
    assert migrations[0].name == "001_first.sql"
