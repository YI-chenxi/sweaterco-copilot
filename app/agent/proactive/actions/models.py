"""Module-local contracts; existing shared contracts remain unchanged."""

import json
import math
from typing import Any, Protocol

ERROR_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "MISSING_FIELD",
        "NOT_FOUND",
        "AMBIGUOUS_TARGET",
        "UNAUTHORIZED",
        "CONFLICT",
        "ALREADY_EXECUTED",
        "EXPIRED",
        "STORAGE_ERROR",
        "EXECUTION_ERROR",
        "UNSUPPORTED_CONDITION",
    }
)
TERMINAL_STATUSES = frozenset({"executed", "failed", "rejected", "expired"})


class ActionError(Exception):
    """A machine-readable failure. Raw provider exception text is never exposed."""

    def __init__(self, code: str) -> None:
        self.code = code if code in ERROR_CODES else "EXECUTION_ERROR"
        super().__init__(self.code)


def json_snapshot(value: Any) -> Any:
    """Detach strict JSON values; reject non-finite numbers and oversized payloads."""

    def check(item: Any, depth: int = 0) -> None:
        if depth > 20:
            raise ActionError("INVALID_ARGUMENT")
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ActionError("INVALID_ARGUMENT")

    check(value)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > 65536:
        raise ActionError("INVALID_ARGUMENT")
    return json.loads(encoded)


class Authorizer(Protocol):
    def __call__(
        self, actor_id: str, workspace_id: str, operation: str, proposal: dict | None
    ) -> bool:
        """Use trusted server identity/permissions, never LLM-supplied identity."""
        ...


class ActionExecutor(Protocol):
    def validate(self, workspace_id: str, command: dict) -> str | None:
        """Read-only validation of all domain fields, authorization-independent.

        For update/cancel return an opaque target revision (required). For create
        return None or a revision of the creation scope. Raise ActionError on
        missing/invalid fields, missing/inactive targets or unsupported operations.
        """
        ...

    def execute(
        self,
        workspace_id: str,
        command: dict,
        *,
        idempotency_key: str,
        expected_version: str | None,
    ) -> dict:
        """Return ServiceResult; enforce version check atomically with the mutation.

        Persist idempotency_key with the business write. The same key must never
        produce a second effect. Return business data only, never credentials.
        """
        ...


class ActionRepository(Protocol):
    """All mutation methods atomically update the proposal AND append its audit event."""

    def create(self, proposal: dict) -> tuple[dict, bool]: ...
    def get(self, workspace_id: str, proposal_id: str) -> dict: ...
    def list_proposals(self, workspace_id: str) -> list[dict]: ...
    def claim(
        self, workspace_id: str, proposal_id: str, actor_id: str, at: str
    ) -> tuple[dict, bool]: ...
    def finish(
        self,
        workspace_id: str,
        proposal_id: str,
        actor_id: str,
        at: str,
        *,
        status: str,
        result: dict | None,
        error_code: str | None,
    ) -> dict: ...
    def reject(self, workspace_id: str, proposal_id: str, actor_id: str, at: str) -> dict: ...
    def history(self, workspace_id: str) -> list[dict]: ...
