# SweaterCo Scheduled Morning Briefing (P3)

## Overview

This module implements the backend for scheduled morning briefings in the SweaterCo General Manager's Co-Pilot.

It reuses the existing deterministic Morning Briefing workflow without modifying the original A1/A2 business tools or requiring an LLM during scheduled execution.

## Module Structure

| File | Responsibility |
|---|---|
| `models.py` | Schedule and execution record data models |
| `scheduler.py` | Timezone-aware, start-date-anchored recurrence calculation |
| `repository.py` | Storage interface |
| `memory_repository.py` | In-memory repository for isolated testing |
| `sqlite_repository.py` | Local persistent SQLite repository |
| `executor.py` | Adapter for the existing Morning Briefing workflow |
| `service.py` | Schedule management and due execution |
| `runner.py` | Standalone command-line execution entry point |

## Public Service Interface

```python
class BriefingService:
    def create_schedule(self, workspace_id: str, parameters: dict) -> dict: ...
    def update_schedule(self, workspace_id: str, schedule_id: str, parameters: dict) -> dict: ...
    def cancel_schedule(self, workspace_id: str, schedule_id: str) -> dict: ...
    def list_schedules(self, workspace_id: str) -> list[dict]: ...
    def list_history(self, workspace_id: str, schedule_id: str | None = None) -> list[dict]: ...
    def run_due_briefings(self, now: datetime) -> list[dict]: ...
```

Mutating methods return JSON-compatible ServiceResult dictionaries. List methods return lists of dictionaries.

## Usage Example

```python
from app.agent.proactive.briefing.service import BriefingService
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository

repository = SQLiteBriefingRepository("briefings.db")
service = BriefingService(repository=repository)

created = service.create_schedule(
    "demo-workspace",
    {
        "interval_days": 3,
        "local_time": "08:00",
        "timezone": "Asia/Singapore",
        "start_date": "2026-11-04",
        "enabled": True,
    },
)

if created["ok"]:
    schedule_id = created["id"]

    service.update_schedule(
        "demo-workspace",
        schedule_id,
        {"interval_days": 7},
    )

    schedules = service.list_schedules("demo-workspace")
    history = service.list_history("demo-workspace", schedule_id)
```

## Schedule Semantics

- `interval_days` must be a positive integer; UI presets are 1, 3 and 7 days.
- Recurrence is anchored to `start_date`.
- Default timezone is `Asia/Singapore`.
- Persistent timestamps use timezone-aware UTC.
- Pausing a schedule sets `enabled=False` and clears `next_run_at`.
- Resuming recalculates `next_run_at`.
- Cancellation is permanent in V1 and retains execution history.
- A cancelled schedule cannot be resumed through the service.
- Failed briefing generation is recorded as `failed`, with no fabricated successful briefing.

## Execution and Persistence

The scheduler queries due tasks, acquires a time-limited claim token, runs the existing deterministic briefing workflow, and atomically saves the execution record while advancing the schedule.

The SQLite repository provides:

- Persistent schedules and run history.
- Workspace-scoped reads and updates.
- Five-minute claim leases.
- Claim invalidation on schedule updates.
- Atomic completion transactions.
- Duplicate occurrence protection.
- Basic schema migration for `cancelled_at`.

The in-memory repository supports isolated tests but is not durable across restarts.

## Standalone Runner

Run one due-task check:

```bash
python -m app.agent.proactive.briefing.runner --database /path/to/briefings.db
```

Or configure `BRIEFING_DB_PATH` and run:

```bash
python -m app.agent.proactive.briefing.runner
```

Exit codes:

- `0`: No reported execution errors.
- `1`: Runner or task execution error.
- `2`: Missing database configuration.

The runner performs one check and exits. It does not independently schedule future invocations.

## Tests

P3 tests:

```bash
python -m pytest tests/proactive/briefing/ -q
```

Full regression suite:

```bash
python -m pytest -q
```

Lint:

```bash
python -m ruff check app/agent/proactive/briefing/ tests/proactive/briefing/
```

Last verified locally: 105 P3 tests passed; 642 full-project tests passed; Ruff passed.

## Integration Requirements

1. P1 parses natural-language commands into normalized parameters.
2. P4 confirms mutating actions before calling P3.
3. Integration connects the service to AgentService and Streamlit.
4. A trusted scheduler invokes the standalone runner independently of the browser.
5. Persistent production storage and server-side workspace authorization must be configured.
6. UI displays schedule status, next execution time, stored briefings and execution history.

## Known Limitations

- SQLite is intended for local development; it is not a reliable shared persistence solution on ephemeral cloud filesystems.
- No external scheduler has been deployed as part of P3.
- No email, push notification or voice delivery is implemented.
- Existing CSV datasets may be static; changing the reference date does not refresh source data.
- The current five-minute lease does not guarantee exactly-once analysis execution.
- Storage failure may require retry after lease expiry.
- User authentication and authorization are integration responsibilities; `workspace_id` alone is not authentication.
- PostgreSQL migration and deployment configuration are outside P3's independent scope.
- Additional production hardening may be required for concurrent schedule updates, long-running executions and timezone DST edge cases.
