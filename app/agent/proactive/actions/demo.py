"""Offline P4 demo using a simulated business executor, not live P3/P5 services."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .models import ActionError
from .repository import SQLiteActionRepository
from .service import ActionService


class DemoExecutor:
    """Simulation only: receipts live in memory; no factory or scheduled task is changed."""

    def __init__(self) -> None:
        self.calls = 0
        self.receipts: dict[str, dict] = {}

    def validate(self, workspace_id: str, command: dict) -> None:
        required = {"interval_days", "local_time", "start_date"}
        if not required <= command["parameters"].keys():
            raise ActionError("MISSING_FIELD")

    def execute(
        self,
        workspace_id: str,
        command: dict,
        *,
        idempotency_key: str,
        expected_version: str | None,
    ) -> dict:
        if idempotency_key not in self.receipts:
            self.calls += 1
            self.receipts[idempotency_key] = {
                "ok": True,
                "status": "simulated_created",
                "id": str(uuid4()),
                "data": {"simulation": True},
                "error_code": None,
            }
        return deepcopy(self.receipts[idempotency_key])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=":memory:",
        help="Optional local SQLite path; default is an isolated in-memory demonstration.",
    )
    args = parser.parse_args()
    if args.database != ":memory:":
        Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    executor = DemoExecutor()
    with SQLiteActionRepository(args.database) as repository:
        service = ActionService(
            repository,
            {("briefing", "create"): executor},
            actor_provider=lambda: "local-demo-operator",
            authorizer=lambda actor, workspace, *_: (
                actor == "local-demo-operator" and workspace == "local-demo"
            ),
        )
        command = {
            "command_type": "create",
            "task_type": "briefing",
            "parameters": {
                "interval_days": 1,
                "local_time": "09:00",
                "start_date": "2026-11-04",
                "timezone": "Asia/Singapore",
            },
            "missing_fields": [],
            "language": "en",
        }
        first = service.propose_action("local-demo", command)
        assert first["ok"] and executor.calls == 0
        confirmed = service.confirm_action("local-demo", first["id"])
        repeated = service.confirm_action("local-demo", first["id"])
        second = service.propose_action("local-demo", command)
        rejected = service.reject_action("local-demo", second["id"])
        assert confirmed["ok"] and repeated["data"]["already_executed"]
        assert rejected["status"] == "rejected" and executor.calls == 1
        print(
            json.dumps(
                {
                    "simulation": True,
                    "proposal": first["status"],
                    "confirmation": confirmed["status"],
                    "repeat": repeated["status"],
                    "rejected": rejected["status"],
                    "business_calls": executor.calls,
                    "audit_events": [
                        event["event"] for event in service.get_action_history("local-demo")
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
