"""Contract management and one-pass local due checks; no external API calls.

Only trusted, confirmed P4 executors may expose mutations. The injected action-ID
provider supplies retry identity, NOT authentication or proof of confirmation.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.agent.proactive.watches.evaluation import prepare_evaluation
from app.agent.proactive.watches.models import Watch, WatchValidationError, require_utc
from app.agent.proactive.watches.order_source import OrderSource
from app.agent.proactive.watches.repository import (
    CancellationContext,
    RepositoryConflict,
    RepositoryError,
    WatchRepository,
    validate_action_id,
    validate_workspace,
)
from app.agent.proactive.watches.rules import validate_parameters
from app.agent.proactive.watches.serialization import (
    legacy_cancel_key,
)
from app.agent.proactive.watches.serialization import (
    record_data as _alert_data,
)
from app.agent.proactive.watches.serialization import (
    watch_data as _watch_data,
)

_LOG = logging.getLogger(__name__)


def _result(
    *,
    ok: bool,
    status: str,
    record_id: str | None = None,
    data: dict[str, Any] | None = None,
    error_code: str | None = None,
    message: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "id": record_id,
        "data": data if data is not None else {},
        "error_code": error_code,
        "message": message,
    }


def _invalid(error: WatchValidationError) -> dict[str, Any]:
    return _result(ok=False, status="invalid", error_code=error.error_code, message=str(error))


class WatchService:
    """Repository commits mutations and replayable receipts; public signatures stay unchanged."""

    def __init__(
        self,
        repository: WatchRepository,
        *,
        action_id_provider: Callable[[], str | None] | None = None,
        id_factory: Callable[[], str] | None = None,
        cancellation_context_provider: Callable[[], CancellationContext | None] | None = None,
        order_source: OrderSource | None = None,
        run_workspaces: tuple[str, ...] = (),
        clock: Callable[[], datetime] | None = None,
        record_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(run_workspaces, tuple):
            raise WatchValidationError("run_workspaces must be an explicit tuple")
        for workspace in run_workspaces:
            validate_workspace(workspace)
        self._run_workspaces = tuple(dict.fromkeys(run_workspaces))
        self._order_source = order_source
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._record_id_factory = record_id_factory or (lambda: str(uuid4()))
        self.repository = repository
        self._cancellation_context_provider = cancellation_context_provider
        self._action_id_provider = action_id_provider or (lambda: None)
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def create_watch(self, workspace_id: str, parameters: dict) -> dict:
        try:
            validate_workspace(workspace_id)
            values = validate_parameters(parameters)
        except WatchValidationError as exc:
            return _invalid(exc)
        try:
            action_id = self._action_id_provider()
            validate_action_id(action_id)
        except WatchValidationError as exc:
            return _invalid(exc)
        except Exception:
            return _result(
                ok=False,
                status="error",
                error_code="EXECUTION_ERROR",
                message="Unable to obtain confirmed-action context.",
            )
        try:
            watch = Watch(id=self._id_factory(), workspace_id=workspace_id, **values)
        except WatchValidationError as exc:
            return _invalid(exc)
        except Exception:
            return _result(
                ok=False,
                status="error",
                error_code="EXECUTION_ERROR",
                message="Unable to create a watch identifier.",
            )
        try:
            stored, newly_created = self.repository.create_watch(watch, action_id)
            receipt = self.repository.get_receipt(workspace_id, action_id)
            if not isinstance(receipt, dict):
                raise RepositoryError("Creation receipt unavailable")
        except RepositoryConflict:
            return _result(
                ok=False,
                status="conflict",
                error_code="CONFLICT",
                message="Action or identifier conflicts with an existing watch.",
            )
        except Exception:
            return _result(
                ok=False,
                status="error",
                error_code="STORAGE_ERROR",
                message="Unable to save watch.",
            )
        if newly_created and self._order_source is not None:
            try:
                # Creation receipt has already committed. Bootstrap is a separate atomic check.
                self._run_one(workspace_id, stored.id, self._clock())
            except Exception:
                _LOG.warning("Initial P5 watch check deferred; creation remains committed.")
        return receipt

    def cancel_watch(self, workspace_id: str, watch_id: str) -> dict:
        try:
            validate_workspace(workspace_id)
            if not isinstance(watch_id, str):
                raise ValueError
            canonical_id = str(UUID(watch_id))
        except WatchValidationError as exc:
            return _invalid(exc)
        except ValueError:
            return _invalid(WatchValidationError("watch_id must be a UUID string"))
        context = None
        if self._cancellation_context_provider is not None:
            try:
                context = self._cancellation_context_provider()
                if not isinstance(context, CancellationContext):
                    raise WatchValidationError("Conditional cancellation context is required")
            except WatchValidationError as exc:
                return _invalid(exc)
            except Exception:
                return _result(
                    ok=False,
                    status="error",
                    error_code="EXECUTION_ERROR",
                    message="Unable to obtain cancellation context.",
                )
        try:
            if context is not None:
                watch = self.repository.cancel_watch_if_version(
                    workspace_id,
                    canonical_id,
                    context.expected_version,
                    context.idempotency_key,
                )
                key = context.idempotency_key
            else:
                watch = self.repository.cancel_watch(workspace_id, canonical_id)
                key = legacy_cancel_key(canonical_id)
            receipt = self.repository.get_receipt(workspace_id, key) if watch is not None else None
            if watch is not None and not isinstance(receipt, dict):
                raise RepositoryError("Cancellation receipt unavailable")
        except RepositoryConflict:
            return _result(
                ok=False,
                status="conflict",
                error_code="CONFLICT",
                message="Watch conflicts with the requested cancellation.",
            )
        except Exception:
            return _result(
                ok=False,
                status="error",
                error_code="STORAGE_ERROR",
                message="Unable to cancel watch.",
            )
        if watch is None:
            return _result(
                ok=False,
                status="not_found",
                error_code="NOT_FOUND",
                message="Watch not found.",
            )
        return receipt

    def list_watches(self, workspace_id: str) -> list[dict]:
        validate_workspace(workspace_id)
        try:
            return [_watch_data(w) for w in self.repository.list_watches(workspace_id)]
        except Exception:
            raise RepositoryError("Unable to list watches") from None

    def list_alerts(self, workspace_id: str) -> list[dict]:
        validate_workspace(workspace_id)
        try:
            return [_alert_data(a) for a in self.repository.list_alerts(workspace_id)]
        except Exception:
            raise RepositoryError("Unable to list alerts") from None

    def _run_one(self, workspace_id: str, watch_id: str, now: datetime) -> dict | None:
        require_utc(now, "now")

        def evaluate(watch, runtime):
            try:
                observation = self._order_source.fetch(workspace_id, watch.order_id)
            except Exception:
                observation = None
            finished_at = self._clock()
            require_utc(finished_at, "observation clock")
            observed_at = max(now, finished_at)
            return prepare_evaluation(
                watch,
                runtime,
                observation,
                now=observed_at,
                record_id=self._record_id_factory(),
                alert_id=self._record_id_factory(),
            )

        return self.repository.process_due(workspace_id, watch_id, now, evaluate)

    def run_due_watches(self, now: datetime) -> list[dict]:
        """One current observation per due Watch, restricted to explicit trusted workspaces."""
        require_utc(now, "now")
        if self._order_source is None or not self._run_workspaces:
            raise WatchValidationError(
                "An order source and explicit runner workspace scope are required",
            )
        results = []
        for workspace in self._run_workspaces:
            try:
                due_ids = self.repository.list_due_ids(workspace, now)
            except Exception:
                results.append(
                    _result(
                        ok=False,
                        status="error",
                        error_code="STORAGE_ERROR",
                        message="Unable to list due watches.",
                    )
                )
                continue
            for watch_id in due_ids:
                try:
                    result = self._run_one(workspace, watch_id, now)
                    if result is not None:
                        results.append(result)
                except Exception:
                    results.append(
                        _result(
                            ok=False,
                            status="error",
                            record_id=watch_id,
                            error_code="STORAGE_ERROR",
                            message="Unable to persist watch check; outcome requires verification.",
                        )
                    )
        return results
