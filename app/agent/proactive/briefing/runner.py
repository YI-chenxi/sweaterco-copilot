"""Standalone entry point for scheduled morning briefings."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.agent.proactive.briefing.service import BriefingService
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository


def main(argv: list[str] | None = None) -> int:
    """Execute due briefings once and return a process exit code."""
    parser = argparse.ArgumentParser(
        description="Run due SweaterCo morning briefings."
    )
    parser.add_argument(
        "--database",
        default=os.getenv("BRIEFING_DB_PATH"),
        help="Path to the SQLite database.",
    )
    args = parser.parse_args(argv)

    if not args.database:
        print(
            "BRIEFING_DB_PATH or --database is required.",
            file=sys.stderr,
        )
        return 2

    try:
        repository = SQLiteBriefingRepository(Path(args.database))
        service = BriefingService(repository=repository)
        results = service.run_due_briefings(datetime.now(timezone.utc))
    except Exception as exc:
        print(
            f"Briefing runner failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(results, ensure_ascii=False))

    if any(not result.get("ok", False) for result in results):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
