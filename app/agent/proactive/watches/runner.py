"""Explicit single-workspace, single-pass CSV watch runner. No daemon or scheduler."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.agent.proactive.watches.order_source import OrderLookupSource
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository
from app.tools.order_lookup import OrderLookupTool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run P5 due checks once using a local CSV snapshot",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--orders",
        type=Path,
        help="Explicit workspace CSV; defaults to demo orders.csv",
    )
    args = parser.parse_args(argv)
    try:
        source = OrderLookupSource(args.workspace, OrderLookupTool(args.orders))
        with SQLiteWatchRepository(args.database) as repo:
            service = WatchService(repo, order_source=source, run_workspaces=(args.workspace,))
            results = service.run_due_watches(datetime.now(timezone.utc))
        print(json.dumps(results, ensure_ascii=False, allow_nan=False))
        print("Source: local CSV snapshot, not live order monitoring.", file=sys.stderr)
        return int(any(not result["ok"] or result["status"] == "unavailable" for result in results))
    except Exception:
        print(
            "P5 runner failed; verify storage and configuration before retrying.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
