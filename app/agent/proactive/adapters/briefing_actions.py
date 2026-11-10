"""P4 action adapter for scheduled morning briefings."""

from typing import Any

from app.agent.proactive.actions.models import ActionError


class BriefingActionExecutor:
    """Connect P4 action commands to the P3 briefing service."""

    ALLOWED_FIELDS = {
        "interval_days",
        "local_time",
        "timezone",
        "start_date",
        "enabled",
    }

    def __init__(self, briefing_service: Any) -> None:
        self.briefing_service = briefing_service

    def validate(
        self,
        workspace_id: str,
        command: dict,
    ) -> str | None:
        """Validate a proposed action without changing business data."""

        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ActionError("INVALID_ARGUMENT")

        if not isinstance(command, dict):
            raise ActionError("INVALID_ARGUMENT")

        if command.get("task_type") != "briefing":
            raise ActionError("INVALID_ARGUMENT")

        operation = command.get("command_type")
        parameters = command.get("parameters")

        if operation not in {"create", "update", "cancel"}:
            raise ActionError("INVALID_ARGUMENT")

        if not isinstance(parameters, dict):
            raise ActionError("INVALID_ARGUMENT")

        if set(parameters) - self.ALLOWED_FIELDS:
            raise ActionError("INVALID_ARGUMENT")

        if operation == "create":
            if command.get("target_id") is not None:
                raise ActionError("INVALID_ARGUMENT")

            try:
                self.briefing_service._validate_parameters(parameters)
            except (TypeError, ValueError) as exc:
                raise ActionError("INVALID_ARGUMENT") from exc

            return None

        target_id = command.get("target_id")

        if not isinstance(target_id, str) or not target_id.strip():
            raise ActionError("MISSING_FIELD")

        try:
            schedule = self.briefing_service.repository.get_schedule(
                workspace_id, target_id
            )
        except Exception as exc:
            raise ActionError("STORAGE_ERROR") from exc

        if schedule is None:
            raise ActionError("NOT_FOUND")

        if schedule.cancelled_at is not None:
            raise ActionError("CONFLICT")

        if operation == "cancel":
            if parameters:
                raise ActionError("INVALID_ARGUMENT")

        elif operation == "update":
            if not parameters:
                raise ActionError("MISSING_FIELD")

            merged = {
                "interval_days": schedule.interval_days,
                "local_time": (
                    schedule.local_time.strftime("%H:%M")
                    if hasattr(schedule.local_time, "strftime")
                    else schedule.local_time
                ),
                "timezone": schedule.timezone,
                "start_date": (
                    schedule.start_date.isoformat()
                    if hasattr(schedule.start_date, "isoformat")
                    else schedule.start_date
                ),
                "enabled": schedule.enabled,
            }
            merged.update(parameters)

            try:
                self.briefing_service._validate_parameters(merged)
            except (TypeError, ValueError) as exc:
                raise ActionError("INVALID_ARGUMENT") from exc

        version = getattr(schedule, "config_version", None)

        if type(version) is not int or version < 1:
            raise ActionError("CONFLICT")

        return str(version)
    def execute(
        self,
        workspace_id: str,
        command: dict,
        *,
        idempotency_key: str,
        expected_version: str | None,
    ) -> dict:
        """Execute a confirmed action through the durable briefing repository."""
        try:
            return self.briefing_service.repository.execute_confirmed_action(
                workspace_id,
                command,
                idempotency_key=idempotency_key,
                expected_version=expected_version,
            )
        except ValueError as exc:
            code = str(exc)
            if code in {
                "INVALID_ARGUMENT",
                "MISSING_FIELD",
                "NOT_FOUND",
                "CONFLICT",
            }:
                raise ActionError(code) from None
            raise ActionError("EXECUTION_ERROR") from None
        except Exception:
            # A database failure may occur after a write was committed.
            # Never claim that the business mutation definitely failed.
            raise ActionError("STORAGE_ERROR") from None
