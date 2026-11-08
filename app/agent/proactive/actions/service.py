"""Explicit, authorized and auditable action confirmation; no business-tool imports."""

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .models import (
    ERROR_CODES,
    ActionError,
    ActionExecutor,
    ActionRepository,
    Authorizer,
    json_snapshot,
)

_PARAMETERS = {
    "briefing": {"interval_days", "local_time", "timezone", "start_date", "enabled"},
    "watch": {
        "order_id",
        "condition_type",
        "deadline_at",
        "expected_status",
        "check_interval_minutes",
    },
}
_MESSAGES = {
    "pending_confirmation": ("请确认是否执行此操作。", "Please confirm this action."),
    "executed": ("操作已执行。", "The action was executed."),
    "rejected": ("操作已拒绝，未执行。", "The action was rejected without execution."),
    "failed": ("操作失败，请查看错误代码。", "The action failed; see the error code."),
    "INVALID_ARGUMENT": ("请求参数或操作不受支持。", "Invalid arguments or unsupported action."),
    "MISSING_FIELD": ("请先补齐必要信息。", "Required information is missing."),
    "NOT_FOUND": ("未找到指定的提案或目标。", "The proposal or target was not found."),
    "AMBIGUOUS_TARGET": ("请明确指定要操作的目标。", "Specify one exact target."),
    "UNAUTHORIZED": ("你没有执行此操作的权限。", "You are not authorized for this operation."),
    "CONFLICT": (
        "状态已改变或操作正在执行，请先核查。",
        "The state changed or execution already started; check before proceeding.",
    ),
    "ALREADY_EXECUTED": (
        "此操作已执行，不会重复执行。",
        "Already executed; no action was repeated.",
    ),
    "EXPIRED": (
        "提案已过期，未开始新的执行。",
        "The proposal expired; no new execution was started.",
    ),
    "STORAGE_ERROR": (
        "无法确认存储结果，请勿重复创建操作，先核查记录。",
        "Storage outcome unavailable; reconcile before creating another action.",
    ),
    "EXECUTION_ERROR": (
        "无法确认执行成功，请先核查结果。",
        "Execution could not be confirmed; reconcile the outcome.",
    ),
    "UNSUPPORTED_CONDITION": ("暂不支持此监控条件。", "This watch condition is unsupported."),
}


def _message(key: str, language: str = "en") -> str:
    pair = _MESSAGES.get(key, _MESSAGES["EXECUTION_ERROR"])
    return pair[0] if language == "zh" else pair[1]


def _identifier(value: object, *, uuid: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 200:
        raise ActionError("INVALID_ARGUMENT")
    if uuid:
        try:
            return str(UUID(value))
        except ValueError:
            raise ActionError("INVALID_ARGUMENT") from None
    return value


class ActionService:
    """Request-scoped identity is injected; missing identity/policy denies access.

    The four method signatures match the proactive contract. Domain operations are
    injected by (task_type, command_type). No unfinished P3/P5 module is imported.
    """

    def __init__(
        self,
        repository: ActionRepository,
        executors: Mapping[tuple[str, str], ActionExecutor],
        *,
        actor_provider: Callable[[], str | None] | None = None,
        authorizer: Authorizer | None = None,
        clock: Callable[[], datetime] | None = None,
        proposal_ttl: timedelta = timedelta(minutes=15),
        request_key_provider: Callable[[], str | None] | None = None,
    ) -> None:
        if not isinstance(proposal_ttl, timedelta) or proposal_ttl <= timedelta(0):
            raise ValueError("proposal_ttl must be positive")
        if any(
            not isinstance(key, tuple)
            or len(key) != 2
            or key[0] not in _PARAMETERS
            or key[1] not in {"create", "update", "cancel"}
            for key in executors
        ):
            raise ValueError("invalid executor registry")
        self.repository = repository
        self.executors = dict(executors)
        self.actor_provider = actor_provider or (lambda: None)
        self.authorizer = authorizer
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.proposal_ttl = proposal_ttl
        self.request_key_provider = request_key_provider

    def _now(self) -> datetime:
        try:
            value = self.clock()
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError
            return value.astimezone(timezone.utc)
        except Exception:
            raise ActionError("INVALID_ARGUMENT") from None

    @staticmethod
    def _stamp(value: datetime) -> str:
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _actor(self) -> str:
        try:
            return _identifier(self.actor_provider())
        except Exception:
            raise ActionError("UNAUTHORIZED") from None

    def _authorize(
        self, actor: str, workspace: str, operation: str, proposal: dict | None = None
    ) -> None:
        if proposal and operation in {"confirm", "reject"} and proposal["creator_id"] != actor:
            raise ActionError("UNAUTHORIZED")
        try:
            allowed = self.authorizer and self.authorizer(
                actor, workspace, operation, deepcopy(proposal) if proposal else None
            )
        except Exception:
            allowed = False
        if allowed is not True:
            raise ActionError("UNAUTHORIZED")

    def _store(self, method: str, *args, **kwargs):
        try:
            return getattr(self.repository, method)(*args, **kwargs)
        except ActionError:
            raise
        except Exception:
            raise ActionError("STORAGE_ERROR") from None

    def _command(self, raw: dict) -> dict:
        raw = json_snapshot(raw)
        if not isinstance(raw, dict) or set(raw) - {
            "command_type",
            "task_type",
            "parameters",
            "target_id",
            "missing_fields",
            "language",
            "reference_text",
        }:
            raise ActionError("INVALID_ARGUMENT")
        command_type, task_type = raw.get("command_type"), raw.get("task_type")
        if not isinstance(task_type, str) or task_type not in _PARAMETERS:
            raise ActionError("INVALID_ARGUMENT")
        if not isinstance(command_type, str) or command_type not in {"create", "update", "cancel"}:
            raise ActionError("INVALID_ARGUMENT")
        missing = raw.get("missing_fields", [])
        if not isinstance(missing, list) or any(not isinstance(field, str) for field in missing):
            raise ActionError("INVALID_ARGUMENT")
        if missing:
            raise ActionError("MISSING_FIELD")
        parameters = raw.get("parameters", {})
        if not isinstance(parameters, dict) or set(parameters) - _PARAMETERS[task_type]:
            raise ActionError("INVALID_ARGUMENT")
        if any(value is None for value in parameters.values()):
            raise ActionError("MISSING_FIELD")
        if command_type == "cancel" and parameters:
            raise ActionError("INVALID_ARGUMENT")
        if command_type in {"create", "update"} and not parameters:
            raise ActionError("MISSING_FIELD")
        target = raw.get("target_id")
        if command_type == "create":
            if target is not None:
                raise ActionError("INVALID_ARGUMENT")
        else:
            if target is None:
                raise ActionError("MISSING_FIELD")
            target = _identifier(target, uuid=True)
        language = raw.get("language", "en")
        if language not in ("en", "zh"):
            raise ActionError("INVALID_ARGUMENT")
        return {
            "command_type": command_type,
            "task_type": task_type,
            "parameters": parameters,
            "target_id": target,
            "language": language,
        }

    def _executor(self, command: dict) -> ActionExecutor:
        executor = self.executors.get((command["task_type"], command["command_type"]))
        if executor is None:
            raise ActionError("INVALID_ARGUMENT")
        return executor

    @staticmethod
    def _validate(executor: ActionExecutor, workspace: str, command: dict) -> str | None:
        try:
            version = executor.validate(workspace, json_snapshot(command))
            if version is not None:
                version = _identifier(version)
            if command["command_type"] != "create" and version is None:
                raise ActionError("CONFLICT")
            return version
        except ActionError:
            raise
        except Exception:
            raise ActionError("EXECUTION_ERROR") from None

    @staticmethod
    def _public(proposal: dict) -> dict:
        public = deepcopy(proposal)
        public.pop("fingerprint", None)
        public.pop("request_key", None)
        started = public["execution_started_at"] is not None
        public["execution_state"] = (
            "in_progress_or_unknown"
            if started and public["status"] == "pending_confirmation"
            else "finished"
            if started
            else "not_started"
        )
        public["requires_reconciliation"] = public[
            "execution_state"
        ] == "in_progress_or_unknown" or bool((public.get("result") or {}).get("outcome_unknown"))
        return public

    def _reply(self, proposal: dict, *, already_executed: bool = False) -> dict:
        status = proposal["status"]
        code = proposal["error_code"]
        if status == "pending_confirmation" and proposal["execution_started_at"]:
            code = "CONFLICT"
        language = proposal["command"]["language"]
        return {
            "ok": code is None,
            "status": status,
            "id": proposal["id"],
            "data": {
                "proposal": self._public(proposal),
                "already_executed": already_executed,
            },
            "error_code": code,
            "message": _message(
                "ALREADY_EXECUTED" if already_executed else code or status, language
            ),
        }

    @staticmethod
    def _error(error: ActionError) -> dict:
        return {
            "ok": False,
            "status": "error",
            "id": None,
            "data": {},
            "error_code": error.code,
            "message": _message(error.code),
        }

    def propose_action(self, workspace_id: str, command: dict) -> dict:
        try:
            workspace = _identifier(workspace_id)
            actor = self._actor()
            self._authorize(actor, workspace, "propose")
            command = self._command(command)
            executor = self._executor(command)
            version = self._validate(executor, workspace, command)
            now = self._now()
            request_key = self.request_key_provider() if self.request_key_provider else None
            if request_key is not None:
                request_key = _identifier(request_key)
            proposal = {
                "id": str(uuid4()),
                "workspace_id": workspace,
                "creator_id": actor,
                "action_type": f"{command['task_type']}.{command['command_type']}",
                "command": command,
                "target_version": version,
                "idempotency_key": str(uuid4()),
                "request_key": request_key,
                "fingerprint": hashlib.sha256(
                    json.dumps(command, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
                "status": "pending_confirmation",
                "created_at": self._stamp(now),
                "expires_at": self._stamp(now + self.proposal_ttl),
                "execution_started_at": None,
                "finished_at": None,
                "result": None,
                "error_code": None,
            }
            stored, _ = self._store("create", proposal)
            return self._reply(stored, already_executed=stored["status"] == "executed")
        except ActionError as error:
            return self._error(error)
        except Exception:
            return self._error(ActionError("INVALID_ARGUMENT"))

    def _load(self, workspace_id: str, proposal_id: str, operation: str) -> tuple[str, str, dict]:
        workspace = _identifier(workspace_id)
        actor = self._actor()
        proposal_id = _identifier(proposal_id, uuid=True)
        proposal = self._store("get", workspace, proposal_id)
        self._authorize(actor, workspace, operation, proposal)
        return workspace, actor, proposal

    @staticmethod
    def _receipt(raw: dict) -> dict:
        try:
            raw = json_snapshot(raw)
            if (
                not isinstance(raw, dict)
                or type(raw.get("ok")) is not bool
                or not isinstance(raw.get("status"), str)
                or not isinstance(raw.get("data"), dict)
                or raw.get("id") is not None
                and not isinstance(raw["id"], str)
                or raw.get("error_code") is not None
                and raw["error_code"] not in ERROR_CODES
                or raw["ok"]
                and raw.get("error_code") is not None
            ):
                raise ValueError
            return {key: raw.get(key) for key in ("ok", "status", "id", "data", "error_code")}
        except Exception:
            raise ActionError("EXECUTION_ERROR") from None

    def confirm_action(self, workspace_id: str, proposal_id: str) -> dict:
        try:
            workspace, actor, proposal = self._load(workspace_id, proposal_id, "confirm")
            at = self._stamp(self._now())
            proposal, claimed = self._store("claim", workspace, proposal["id"], actor, at)
            if not claimed:
                if proposal["status"] == "rejected":
                    raise ActionError("CONFLICT")
                return self._reply(proposal, already_executed=proposal["status"] == "executed")
            status, result, code = "failed", None, None
            attempted = False
            try:
                executor = self._executor(proposal["command"])
                version = self._validate(executor, workspace, proposal["command"])
                if version != proposal["target_version"]:
                    raise ActionError("CONFLICT")
                self._authorize(actor, workspace, "confirm", proposal)
                at = self._stamp(self._now())
                if at >= proposal["expires_at"]:
                    raise ActionError("EXPIRED")
                attempted = True
                receipt = self._receipt(
                    executor.execute(
                        workspace,
                        json_snapshot(proposal["command"]),
                        idempotency_key=proposal["idempotency_key"],
                        expected_version=proposal["target_version"],
                    )
                )
                result = {"receipt": receipt, "outcome_unknown": False}
                if receipt["ok"]:
                    status = "executed"
                else:
                    code = receipt["error_code"] or "EXECUTION_ERROR"
            except ActionError as error:
                code = error.code
                status = "expired" if code == "EXPIRED" and not attempted else "failed"
                result = {"outcome_unknown": attempted}
            except Exception:
                code, result = "EXECUTION_ERROR", {"outcome_unknown": attempted}
            try:
                at = self._stamp(self._now())
            except ActionError:
                pass  # Keep the last valid UTC instant; never retry a completed business call.
            proposal = self._store(
                "finish",
                workspace,
                proposal["id"],
                actor,
                at,
                status=status,
                result=result,
                error_code=code,
            )
            return self._reply(proposal)
        except ActionError as error:
            return self._error(error)

    def reject_action(self, workspace_id: str, proposal_id: str) -> dict:
        try:
            workspace, actor, proposal = self._load(workspace_id, proposal_id, "reject")
            proposal = self._store(
                "reject", workspace, proposal["id"], actor, self._stamp(self._now())
            )
            if proposal["status"] not in {"rejected", "expired"}:
                raise ActionError(
                    "ALREADY_EXECUTED" if proposal["status"] == "executed" else "CONFLICT"
                )
            return self._reply(proposal)
        except ActionError as error:
            return self._error(error)

    def get_action_history(self, workspace_id: str) -> list[dict]:
        """Read-only list; raise safe ActionError on denial/storage failure, never fake []."""
        workspace = _identifier(workspace_id)
        actor = self._actor()
        self._authorize(actor, workspace, "history")
        return self._store("history", workspace)
