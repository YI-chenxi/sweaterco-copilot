"""In-memory repository for isolated briefing tests."""

from dataclasses import replace
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from app.agent.proactive.briefing.models import BriefingRun, BriefingSchedule


class InMemoryBriefingRepository:
    """Thread-safe test repository; not durable across restarts."""

    def __init__(self) -> None:
        self._schedules: dict[tuple[str, str], BriefingSchedule] = {}
        self._runs: dict[tuple[str, datetime], BriefingRun] = {}
        self._claimed: dict[tuple[str, datetime], tuple[str, datetime]] = {}
        self._lease_duration = timedelta(minutes=5)
        self._lock = RLock()

    def create_schedule(self, schedule: BriefingSchedule) -> None:
        with self._lock:
            if any(key[1] == schedule.id for key in self._schedules):
                raise ValueError("Schedule already exists")
            self._schedules[(schedule.workspace_id, schedule.id)] = replace(schedule)

    def get_schedule(
        self, workspace_id: str, schedule_id: str
    ) -> BriefingSchedule | None:
        with self._lock:
            schedule = self._schedules.get((workspace_id, schedule_id))
            return replace(schedule) if schedule is not None else None

    def update_schedule(self, schedule: BriefingSchedule) -> None:
        with self._lock:
            key = (schedule.workspace_id, schedule.id)
            existing = self._schedules.get(key)

            if existing is None:
                raise ValueError("Schedule not found")

            if existing.cancelled_at is not None:
                raise ValueError("Schedule already cancelled")

            if schedule.config_version != existing.config_version:
                raise ValueError("Schedule version conflict")

            self._schedules[key] = replace(
                schedule,
                config_version=existing.config_version + 1,
            )

            # Invalidate claims created before this schedule update.
            stale_claims = [
                claim_key
                for claim_key in self._claimed
                if claim_key[0] == schedule.id
            ]
            for claim_key in stale_claims:
                self._claimed.pop(claim_key, None)

    def list_schedules(self, workspace_id: str) -> list[BriefingSchedule]:
        with self._lock:
            return [
                replace(schedule)
                for (owner, _), schedule in self._schedules.items()
                if owner == workspace_id
            ]

    def list_due_schedules(self, now: datetime) -> list[BriefingSchedule]:
        with self._lock:
            return [
                replace(schedule)
                for schedule in self._schedules.values()
                if schedule.enabled
                and schedule.next_run_at is not None
                and schedule.next_run_at <= now
            ]

    def claim_due_schedule(
        self, schedule_id: str, scheduled_for: datetime, now: datetime
    ) -> str | None:
        with self._lock:
            matches = [
                schedule
                for schedule in self._schedules.values()
                if schedule.id == schedule_id
            ]
            if len(matches) != 1:
                return None

            schedule = matches[0]
            key = (schedule_id, scheduled_for)

            if (
                not schedule.enabled
                or schedule.next_run_at != scheduled_for
                or scheduled_for > now
                or key in self._runs
            ):
                return None

            existing_claim = self._claimed.get(key)
            if existing_claim is not None:
                _, expires_at = existing_claim
                if now < expires_at:
                    return None

            token = str(uuid4())
            self._claimed[key] = (token, now + self._lease_duration)
            return token

    def save_run(
        self, run: BriefingRun, claim_token: str, now: datetime
    ) -> None:
        with self._lock:
            schedules = [
                schedule
                for schedule in self._schedules.values()
                if schedule.id == run.schedule_id
            ]

            if not schedules:
                raise ValueError("Schedule not found")

            schedule = schedules[0]

            if schedule.workspace_id != run.workspace_id:
                raise ValueError("Run workspace does not match schedule workspace")

            key = (run.schedule_id, run.scheduled_for)

            if key in self._runs:
                raise ValueError("Run already exists")

            claim = self._claimed.get(key)
            if claim is None:
                raise ValueError("No active claim")

            active_token, expires_at = claim
            if claim_token != active_token or now >= expires_at:
                raise ValueError("Invalid or expired claim token")

            if not schedule.enabled or schedule.next_run_at != run.scheduled_for:
                raise ValueError("Schedule is no longer eligible")

            self._runs[key] = replace(run)
            self._claimed.pop(key, None)

    def complete_run(
        self,
        run: BriefingRun,
        claim_token: str,
        now: datetime,
        next_run_at: datetime | None,
    ) -> None:
        """Atomically record a run and advance its schedule in memory."""
        from datetime import timedelta

        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")

        if next_run_at is not None:
            if (
                next_run_at.tzinfo is None
                or next_run_at.utcoffset() != timedelta(0)
                or next_run_at <= run.scheduled_for
            ):
                raise ValueError("next_run_at must be UTC and after scheduled_for")

        with self._lock:
            schedule_key = (run.workspace_id, run.schedule_id)
            schedule = self._schedules.get(schedule_key)

            if schedule is None:
                raise ValueError("Schedule not found")

            key = (run.schedule_id, run.scheduled_for)

            if key in self._runs:
                raise ValueError("Run already exists")

            claim = self._claimed.get(key)
            if claim is None:
                raise ValueError("No active claim")

            active_token, expires_at = claim
            if claim_token != active_token or now >= expires_at:
                raise ValueError("Invalid or expired claim token")

            if (
                not schedule.enabled
                or schedule.cancelled_at is not None
                or schedule.next_run_at != run.scheduled_for
            ):
                raise ValueError("Schedule is no longer eligible")

            updated_schedule = replace(
                schedule,
                next_run_at=next_run_at,
                updated_at=now,
            )

            self._runs[key] = replace(run)
            self._schedules[schedule_key] = updated_schedule
            self._claimed.pop(key, None)

    def list_history(
        self, workspace_id: str, schedule_id: str | None = None
    ) -> list[BriefingRun]:
        with self._lock:
            runs = [
                replace(run)
                for run in self._runs.values()
                if run.workspace_id == workspace_id
                and (schedule_id is None or run.schedule_id == schedule_id)
            ]
            return sorted(runs, key=lambda run: run.executed_at, reverse=True)
