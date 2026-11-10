"""Versioned PostgreSQL migrations with transactional advisory locking."""

import argparse
import hashlib
from pathlib import Path

from app.accounts.storage.database import create_connection_factory

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_LOCK_ID = 572910384


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Find migrations in deterministic version order."""
    migrations = sorted(directory.glob("[0-9]*_*.sql"))

    versions = [path.name.split("_", 1)[0] for path in migrations]

    if len(versions) != len(set(versions)):
        raise ValueError("Duplicate migration version")

    return migrations


def apply_migrations(connection_factory, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations atomically and verify existing checksums."""
    applied = []

    connection = connection_factory()

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (MIGRATION_LOCK_ID,),
                )

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS account_schema_migrations (
                        version TEXT PRIMARY KEY,
                        checksum TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                for path in discover_migrations(directory):
                    version = path.name.split("_", 1)[0]
                    sql = path.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

                    cursor.execute(
                        "SELECT checksum FROM account_schema_migrations WHERE version = %s",
                        (version,),
                    )
                    row = cursor.fetchone()

                    if row is not None:
                        if row[0] != checksum:
                            raise ValueError(
                                f"Migration checksum mismatch: {version}"
                            )
                        continue

                    cursor.execute(sql)
                    cursor.execute(
                        """INSERT INTO account_schema_migrations
                           (version, checksum) VALUES (%s, %s)""",
                        (version, checksum),
                    )
                    applied.append(version)

        return applied

    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply SweaterCo account database migrations."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL URL; defaults to DATABASE_URL.",
    )
    args = parser.parse_args()

    factory = create_connection_factory(args.database_url)
    applied = apply_migrations(factory)

    print("Applied migrations:", ", ".join(applied) if applied else "none")


if __name__ == "__main__":
    main()
