"""P4 action adapter for P5 watches."""

from typing import Any

from app.agent.proactive.actions.models import ActionError
from app.agent.proactive.watches.models import WatchValidationError
from app.agent.proactive.watches.repository import validate_workspace
from app.agent.proactive.watches.rules import validate_parameters


class WatchActionExecutor:
    """Validate watch commands without performing mutations."""

    def __init__(self, watch_service: Any) -> None:
        self.watch_service = watch_service

    def validate(self, workspace_id: str, command: dict) -> str | None:
        if not isinstance(command, dict):
            raise ActionError("INVALID_ARGUMENT")

        if command.get("task_type") != "watch":
            raise ActionError("INVALID_ARGUMENT")

        operation = command.get("command_type")
        if operation not in {"create", "cancel"}:
            raise ActionError("INVALID_ARGUMENT")

        parameters = command.get("parameters")
        if not isinstance(parameters, dict):
            raise ActionError("INVALID_ARGUMENT")

        try:
            validate_workspace(workspace_id)
        except WatchValidationError:
            raise ActionError("INVALID_ARGUMENT") from None

        if operation == "create":
            if command.get("target_id") is not None:
                raise ActionError("INVALID_ARGUMENT")

            try:
                validate_parameters(parameters)
            except WatchValidationError as exc:
                raise ActionError(exc.error_code) from None

            return None

        if parameters:
            raise ActionError("INVALID_ARGUMENT")

        target_id = command.get("target_id")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ActionError("MISSING_FIELD")

        try:
            state = self.watch_service.repository.get_watch_state(
                workspace_id, target_id
            )
        except Exception:
            raise ActionError("STORAGE_ERROR") from None

        if state is None:
            raise ActionError("NOT_FOUND")

        if state.cancelled:
            raise ActionError("CONFLICT")

        return state.version

    def execute(
        self,
        workspace_id: str,
        command: dict,
        *,
        idempotency_key: str,
        expected_version: str | None,
    ) -> dict:
        """Execute a P4-confirmed watch mutation with request-local context."""
        from app.agent.proactive.watches.repository import (
            CancellationContext,
        )
        if not isinstance(command, dict):
            raise ActionError("INVALID_ARGUMENT")

        operation = command.get("command_type")
        if command.get("task_type") != "watch":
            raise ActionError("INVALID_ARGUMENT")

        if operation not in {"create", "cancel"}:
            raise ActionError("INVALID_ARGUMENT")

        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ActionError("INVALID_ARGUMENT")

        if operation == "create":
            if expected_version is not None:
                raise ActionError("INVALID_ARGUMENT")

            request_service = self.watch_service.for_confirmed_action(
                action_id_provider=lambda: idempotency_key,
            )

            result = request_service.create_watch(
                workspace_id,
                command.get("parameters"),
            )

        else:
            if not isinstance(expected_version, str):
                raise ActionError("CONFLICT")

            try:
                context = CancellationContext(
                    idempotency_key=idempotency_key,
                    expected_version=expected_version,
                )
            except WatchValidationError:
                raise ActionError("INVALID_ARGUMENT") from None

            request_service = self.watch_service.for_confirmed_action(
                cancellation_context_provider=lambda: context,
            )

            result = request_service.cancel_watch(
                workspace_id,
                command.get("target_id"),
            )

        if not isinstance(result, dict):
            raise ActionError("EXECUTION_ERROR")

        if result.get("error_code") == "STORAGE_ERROR":
            # P5 may have committed before the receipt became unavailable.
            # P4 must preserve outcome_unknown instead of treating this
            # as a definite business failure.
            raise ActionError("STORAGE_ERROR")

        return result
