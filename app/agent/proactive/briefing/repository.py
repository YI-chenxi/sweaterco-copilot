"""Storage interface for scheduled morning briefings."""

from datetime import datetime
from typing import Protocol

from app.agent.proactive.briefing.models import BriefingRun, BriefingSchedule


class BriefingRepository(Protocol):
    """Persistence operations required by BriefingService."""

    def create_schedule(self, schedule: BriefingSchedule) -> None:
        """Persist a new schedule atomically."""
        ...

    def get_schedule(
        self, workspace_id: str, schedule_id: str
    ) -> BriefingSchedule | None:
        """Find a schedule within its workspace."""
        ...

    def update_schedule(self, schedule: BriefingSchedule) -> None:
        """Persist changes to an existing schedule."""
        ...

    def list_schedules(self, workspace_id: str) -> list[BriefingSchedule]:
        """List schedules belonging to one workspace."""
        ...

    def list_due_schedules(self, now: datetime) -> list[BriefingSchedule]:
        """Find enabled schedules due at or before UTC now."""
        ...

    def claim_due_schedule(
        self, schedule_id: str, scheduled_for: datetime, now: datetime
    ) -> str | None:
        """Atomically claim an occurrence and return a unique token."""
        ...

    def save_run(
        self, run: BriefingRun, claim_token: str, now: datetime
    ) -> None:
        """Save a run only when its claim token is still valid."""
        ...

    def complete_run(
        self,
        run: BriefingRun,
        claim_token: str,
        now: datetime,
        next_run_at: datetime | None,
    ) -> None:
        """Atomically save a run and advance its schedule."""
        ...

    def list_history(
        self, workspace_id: str, schedule_id: str | None = None
    ) -> list[BriefingRun]:
        """Return execution history scoped to a workspace."""
        ...
