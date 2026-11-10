"""Application service for scheduled morning briefings."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.agent.proactive.briefing.executor import generate_morning_briefing
from app.agent.proactive.briefing.repository import BriefingRepository


class BriefingService:
    """Manage schedules and execute due morning briefings."""

    def __init__(
        self,
        repository: BriefingRepository,
        clock: Callable[[], datetime] | None = None,
        briefing_executor: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.briefing_executor = (
            briefing_executor
            if briefing_executor is not None
            else generate_morning_briefing
        )

    def create_schedule(
        self, workspace_id: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate, calculate and persist a new briefing schedule."""
        from dataclasses import asdict
        from uuid import uuid4

        from app.agent.proactive.briefing.models import BriefingSchedule
        from app.agent.proactive.briefing.scheduler import calculate_next_run

        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return {
                "ok": False,
                "status": "invalid",
                "id": None,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": "workspace_id is required.",
            }

        try:
            values = self._validate_parameters(parameters)
            now = self._utc_now()

            next_run = (
                calculate_next_run(
                    values["start_date"],
                    values["local_time"],
                    values["timezone"],
                    values["interval_days"],
                    now,
                )
                if values["enabled"]
                else None
            )

            schedule = BriefingSchedule(
                id=str(uuid4()),
                workspace_id=workspace_id,
                next_run_at=next_run,
                created_at=now,
                updated_at=now,
                **values,
            )

        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "status": "invalid",
                "id": None,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": str(exc),
            }

        try:
            self.repository.create_schedule(schedule)
        except Exception:
            return {
                "ok": False,
                "status": "error",
                "id": None,
                "data": {},
                "error_code": "STORAGE_ERROR",
                "message": "Unable to save briefing schedule.",
            }

        return {
            "ok": True,
            "status": "created",
            "id": schedule.id,
            "data": {
                **asdict(schedule),
                "local_time": schedule.local_time.strftime("%H:%M"),
                "start_date": schedule.start_date.isoformat(),
                "next_run_at": (
                    schedule.next_run_at.isoformat()
                    if schedule.next_run_at is not None
                    else None
                ),
                "created_at": schedule.created_at.isoformat(),
                "updated_at": schedule.updated_at.isoformat(),
            },
            "error_code": None,
            "message": "Briefing schedule created.",
        }

    def _utc_now(self) -> datetime:
        """Return a validated timezone-aware UTC timestamp."""
        now = self.clock()

        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise ValueError("clock must return timezone-aware UTC")

        return now

    @staticmethod
    def _validate_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize briefing schedule parameters."""
        from datetime import date, time
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        interval = parameters.get("interval_days")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
            raise ValueError("interval_days must be a positive integer")

        local_time_value = parameters.get("local_time")
        if not isinstance(local_time_value, str):
            raise ValueError("local_time must be in HH:MM format")

        try:
            parsed_time = time.fromisoformat(local_time_value)
        except ValueError as exc:
            raise ValueError("local_time must be in HH:MM format") from exc

        if (
            len(local_time_value) != 5
            or local_time_value[2] != ":"
            or parsed_time.strftime("%H:%M") != local_time_value
        ):
            raise ValueError("local_time must be in HH:MM format")

        timezone_name = parameters.get("timezone", "Asia/Singapore")
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise ValueError("Invalid IANA timezone") from exc

        start_date_value = parameters.get("start_date")
        if not isinstance(start_date_value, str):
            raise ValueError("start_date must be an ISO date")

        try:
            parsed_date = date.fromisoformat(start_date_value)
        except ValueError as exc:
            raise ValueError("start_date must be an ISO date") from exc

        enabled = parameters.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")

        return {
            "interval_days": interval,
            "local_time": parsed_time,
            "timezone": timezone_name,
            "start_date": parsed_date,
            "enabled": enabled,
        }

    def update_schedule(
        self,
        workspace_id: str,
        schedule_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an existing briefing schedule within its workspace."""
        from dataclasses import asdict, replace

        from app.agent.proactive.briefing.scheduler import calculate_next_run

        if (
            not isinstance(workspace_id, str)
            or not workspace_id.strip()
            or not isinstance(schedule_id, str)
            or not schedule_id.strip()
        ):
            return {
                "ok": False,
                "status": "invalid",
                "id": None,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": "workspace_id and schedule_id are required.",
            }

        try:
            existing = self.repository.get_schedule(workspace_id, schedule_id)
        except Exception:
            return {
                "ok": False,
                "status": "error",
                "id": schedule_id,
                "data": {},
                "error_code": "STORAGE_ERROR",
                "message": "Unable to load briefing schedule.",
            }

        if existing is None:
            return {
                "ok": False,
                "status": "not_found",
                "id": schedule_id,
                "data": {},
                "error_code": "NOT_FOUND",
                "message": "Briefing schedule not found.",
            }

        if existing.cancelled_at is not None:
            return {
                "ok": False,
                "status": "conflict",
                "id": schedule_id,
                "data": {},
                "error_code": "CONFLICT",
                "message": "Cancelled schedules cannot be modified.",
            }

        allowed_fields = {
            "interval_days", "local_time", "timezone",
            "start_date", "enabled",
        }

        if not isinstance(parameters, dict) or not parameters:
            return {
                "ok": False,
                "status": "invalid",
                "id": schedule_id,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": "Update parameters are required.",
            }

        if set(parameters) - allowed_fields:
            return {
                "ok": False,
                "status": "invalid",
                "id": schedule_id,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": "Unsupported schedule update field.",
            }

        merged = {
            "interval_days": existing.interval_days,
            "local_time": existing.local_time.strftime("%H:%M"),
            "timezone": existing.timezone,
            "start_date": existing.start_date.isoformat(),
            "enabled": existing.enabled,
        }
        merged.update(parameters)

        try:
            values = self._validate_parameters(merged)
            now = self._utc_now()

            next_run = (
                calculate_next_run(
                    values["start_date"],
                    values["local_time"],
                    values["timezone"],
                    values["interval_days"],
                    now,
                )
                if values["enabled"]
                else None
            )

            updated = replace(
                existing,
                **values,
                next_run_at=next_run,
                updated_at=now,
            )
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "status": "invalid",
                "id": schedule_id,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": str(exc),
            }

        try:
            self.repository.update_schedule(updated)
        except Exception:
            return {
                "ok": False,
                "status": "error",
                "id": schedule_id,
                "data": {},
                "error_code": "STORAGE_ERROR",
                "message": "Unable to update briefing schedule.",
            }

        # The repository atomically increments config_version.
        # Reflect the committed version in the returned business result.
        updated = replace(
            updated,
            config_version=updated.config_version + 1,
        )

        data = asdict(updated)
        data.update({
            "local_time": updated.local_time.strftime("%H:%M"),
            "start_date": updated.start_date.isoformat(),
            "next_run_at": (
                updated.next_run_at.isoformat()
                if updated.next_run_at is not None
                else None
            ),
            "created_at": updated.created_at.isoformat(),
            "updated_at": updated.updated_at.isoformat(),
        })

        return {
            "ok": True,
            "status": "updated",
            "id": schedule_id,
            "data": data,
            "error_code": None,
            "message": "Briefing schedule updated.",
        }

    def cancel_schedule(
        self, workspace_id: str, schedule_id: str
    ) -> dict[str, Any]:
        """Permanently cancel a schedule without deleting its history."""
        from dataclasses import replace

        if (
            not isinstance(workspace_id, str)
            or not workspace_id.strip()
            or not isinstance(schedule_id, str)
            or not schedule_id.strip()
        ):
            return {
                "ok": False,
                "status": "invalid",
                "id": None,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": "workspace_id and schedule_id are required.",
            }

        try:
            existing = self.repository.get_schedule(workspace_id, schedule_id)
        except Exception:
            return {
                "ok": False,
                "status": "error",
                "id": schedule_id,
                "data": {},
                "error_code": "STORAGE_ERROR",
                "message": "Unable to load briefing schedule.",
            }

        if existing is None:
            return {
                "ok": False,
                "status": "not_found",
                "id": schedule_id,
                "data": {},
                "error_code": "NOT_FOUND",
                "message": "Briefing schedule not found.",
            }

        if existing.cancelled_at is not None:
            return {
                "ok": False,
                "status": "conflict",
                "id": schedule_id,
                "data": {},
                "error_code": "CONFLICT",
                "message": "Briefing schedule is already cancelled.",
            }

        try:
            now = self._utc_now()
            cancelled = replace(
                existing,
                enabled=False,
                next_run_at=None,
                cancelled_at=now,
                updated_at=now,
            )
        except (TypeError, ValueError):
            return {
                "ok": False,
                "status": "error",
                "id": schedule_id,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": "Invalid schedule cancellation time.",
            }

        try:
            self.repository.update_schedule(cancelled)
        except Exception:
            return {
                "ok": False,
                "status": "error",
                "id": schedule_id,
                "data": {},
                "error_code": "STORAGE_ERROR",
                "message": "Unable to cancel briefing schedule.",
            }

        return {
            "ok": True,
            "status": "cancelled",
            "id": schedule_id,
            "data": {
                "enabled": False,
                "next_run_at": None,
                "cancelled_at": now.isoformat(),
            },
            "error_code": None,
            "message": "Briefing schedule cancelled.",
        }

    def list_schedules(self, workspace_id: str) -> list[dict]:
        """Return JSON-compatible schedules for one workspace."""
        from dataclasses import asdict

        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return []

        schedules = self.repository.list_schedules(workspace_id)

        results = []
        for schedule in schedules:
            data = asdict(schedule)
            data.update({
                "local_time": schedule.local_time.strftime("%H:%M"),
                "start_date": schedule.start_date.isoformat(),
                "next_run_at": (
                    schedule.next_run_at.isoformat()
                    if schedule.next_run_at is not None
                    else None
                ),
                "created_at": schedule.created_at.isoformat(),
                "updated_at": schedule.updated_at.isoformat(),
                "cancelled_at": (
                    schedule.cancelled_at.isoformat()
                    if schedule.cancelled_at is not None
                    else None
                ),
            })
            results.append(data)

        return results

    def list_history(
        self, workspace_id: str, schedule_id: str | None = None
    ) -> list[dict]:
        """Return JSON-compatible briefing execution history."""
        from dataclasses import asdict

        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return []

        if schedule_id is not None:
            if not isinstance(schedule_id, str) or not schedule_id.strip():
                return []

        runs = self.repository.list_history(workspace_id, schedule_id)

        results = []
        for run in runs:
            data = asdict(run)
            data.update({
                "scheduled_for": run.scheduled_for.isoformat(),
                "executed_at": run.executed_at.isoformat(),
            })
            results.append(data)

        return results

    def run_due_briefings(self, now: datetime) -> list[dict]:
        """Generate due briefings and atomically record their results."""
        from dataclasses import asdict
        from uuid import uuid4
        from zoneinfo import ZoneInfo

        from app.agent.proactive.briefing.models import BriefingRun
        from app.agent.proactive.briefing.scheduler import calculate_next_run

        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise ValueError("now must be timezone-aware UTC")

        results = []
        due_schedules = self.repository.list_due_schedules(now)

        for schedule in due_schedules:
            scheduled_for = schedule.next_run_at
            if scheduled_for is None:
                continue

            token = self.repository.claim_due_schedule(
                schedule.id, scheduled_for, now
            )
            if token is None:
                continue

            reference_date = now.astimezone(
                ZoneInfo(schedule.timezone)
            ).date()

            try:
                briefing = self.briefing_executor(reference_date)
                if not isinstance(briefing, dict):
                    raise ValueError("Briefing executor returned invalid data")

                # Ensure the result can be stored as JSON.
                import json
                json.dumps(briefing)

                status = "success"
                error = None
                result_data = briefing

            except Exception:
                status = "failed"
                error = "Morning briefing generation failed."
                result_data = {}

            next_run = calculate_next_run(
                schedule.start_date,
                schedule.local_time,
                schedule.timezone,
                schedule.interval_days,
                now,
                inclusive=False,
            )

            run = BriefingRun(
                id=str(uuid4()),
                workspace_id=schedule.workspace_id,
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                executed_at=now,
                status=status,
                result=result_data,
                error=error,
            )

            try:
                self.repository.complete_run(
                    run, token, now, next_run
                )
            except ValueError:
                # The claim expired or the schedule changed.
                continue
            except Exception:
                results.append({
                    "ok": False,
                    "status": "error",
                    "id": run.id,
                    "data": {
                        "schedule_id": schedule.id,
                        "scheduled_for": scheduled_for.isoformat(),
                        "persisted": False,
                    },
                    "error_code": "STORAGE_ERROR",
                    "message": "Unable to save briefing execution.",
                })
                continue

            results.append({
                "ok": status == "success",
                "status": status,
                "id": run.id,
                "data": {
                    **asdict(run),
                    "scheduled_for": scheduled_for.isoformat(),
                    "executed_at": now.isoformat(),
                    "next_run_at": next_run.isoformat(),
                },
                "error_code": (
                    None if status == "success" else "EXECUTION_ERROR"
                ),
                "message": (
                    "Morning briefing completed."
                    if status == "success"
                    else "Morning briefing generation failed."
                ),
            })

        return results
