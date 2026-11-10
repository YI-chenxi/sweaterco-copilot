"""Composition root and guarded natural-language routing for proactive work."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.agent.proactive.actions.models import ActionError
from app.agent.proactive.actions.repository import SQLiteActionRepository
from app.agent.proactive.actions.service import ActionService
from app.agent.proactive.adapters.briefing_actions import BriefingActionExecutor
from app.agent.proactive.adapters.watch_actions import WatchActionExecutor
from app.agent.proactive.briefing.service import BriefingService
from app.agent.proactive.briefing.sqlite_repository import SQLiteBriefingRepository
from app.agent.proactive.understanding.parser import ProactiveCommandParser
from app.agent.proactive.watches.order_source import OrderLookupSource
from app.agent.proactive.watches.serialization import record_data
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository
from app.tools.order_lookup import OrderLookupTool


class ProactiveApplication:
    """Request identity and authorization are supplied by the trusted host app."""

    def __init__(
        self,
        *,
        parser: ProactiveCommandParser,
        actions: ActionService,
        briefings: BriefingService,
        watches: WatchService,
        actor_provider: Callable[[], str | None],
        workspace_provider: Callable[[], str | None],
    ) -> None:
        self.parser = parser
        self.actions = actions
        self.briefings = briefings
        self.watches = watches
        self.actor_provider = actor_provider
        self.workspace_provider = workspace_provider

    def _context(self, workspace: str) -> dict[str, Any]:
        schedules = self.briefings.list_schedules(workspace)
        watches = self.watches.list_watches(workspace)
        pending = self.actions.get_pending_proposals(workspace)
        return {
            "briefings": [{"id": item["id"]} for item in schedules if not item.get("cancelled_at")],
            "watches": [{"id": item["id"]} for item in watches if not item.get("cancelled")],
            "proposals": [{"id": item["id"]} for item in pending],
        }

    def _workspace(self) -> str:
        actor, workspace = self.actor_provider(), self.workspace_provider()
        if not actor or not workspace:
            raise ActionError("UNAUTHORIZED")
        return workspace

    def list_briefings(self) -> dict:
        """Page-facing read API; the workspace comes from trusted request context."""
        try:
            workspace = self._workspace()
            return {
                "ok": True,
                "status": "listed",
                "data": {
                    "items": self.briefings.list_schedules(workspace),
                    "history": self.briefings.list_history(workspace),
                },
            }
        except ActionError as error:
            return self._error_result(error.code)
        except Exception:
            return self._error_result("STORAGE_ERROR")

    def get_briefing(self, schedule_id: str) -> dict:
        try:
            workspace = self._workspace()
            item = next(
                (
                    row
                    for row in self.briefings.list_schedules(workspace)
                    if row["id"] == schedule_id
                ),
                None,
            )
            if item is None:
                return self._error_result("NOT_FOUND")
            return {
                "ok": True,
                "status": "found",
                "id": schedule_id,
                "data": {
                    "item": item,
                    "history": self.briefings.list_history(workspace, schedule_id),
                },
            }
        except ActionError as error:
            return self._error_result(error.code)
        except Exception:
            return self._error_result("STORAGE_ERROR")

    def list_watches(self) -> dict:
        try:
            workspace = self._workspace()
            return {
                "ok": True,
                "status": "listed",
                "data": {
                    "items": self.watches.list_watches(workspace),
                    "alerts": self.watches.list_alerts(workspace),
                    "checks": [
                        record_data(record)
                        for record in self.watches.repository.list_checks(workspace)
                    ],
                },
            }
        except ActionError as error:
            return self._error_result(error.code)
        except Exception:
            return self._error_result("STORAGE_ERROR")

    def get_watch(self, watch_id: str) -> dict:
        try:
            workspace = self._workspace()
            item = next(
                (row for row in self.watches.list_watches(workspace) if row["id"] == watch_id),
                None,
            )
            if item is None:
                return self._error_result("NOT_FOUND")
            return {"ok": True, "status": "found", "id": watch_id, "data": {"item": item}}
        except ActionError as error:
            return self._error_result(error.code)
        except Exception:
            return self._error_result("STORAGE_ERROR")

    def list_pending_actions(self) -> dict:
        try:
            workspace = self._workspace()
            return {
                "ok": True,
                "status": "listed",
                "data": {"items": self.actions.get_pending_proposals(workspace)},
            }
        except ActionError as error:
            return self._error_result(error.code)

    def get_action(self, proposal_id: str) -> dict:
        try:
            workspace = self._workspace()
            item = self.actions.get_proposal(workspace, proposal_id)
            return {"ok": True, "status": "found", "id": proposal_id, "data": {"item": item}}
        except ActionError as error:
            return self._error_result(error.code)

    def list_action_history(self) -> dict:
        try:
            workspace = self._workspace()
            events = self.actions.get_action_history(workspace)
            groups: dict[str, dict[str, Any]] = {}
            for event in events:
                group = groups.setdefault(
                    event["proposal_id"],
                    {
                        "id": event["proposal_id"],
                        "action_type": event["action_type"],
                        "target_id": event["target_id"],
                        "parameters": event["parameters"],
                        "status": event["status"],
                        "result": event["result"],
                        "events": [],
                    },
                )
                group["events"].append(event)
                group["status"] = event["status"]
                group["result"] = event["result"]
            return {"ok": True, "status": "listed", "data": {"items": list(groups.values())}}
        except ActionError as error:
            return self._error_result(error.code)

    def propose_action(self, command: dict) -> dict:
        try:
            return self.actions.propose_action(self._workspace(), command)
        except ActionError as error:
            return self._error_result(error.code)

    def confirm_action(self, proposal_id: str) -> dict:
        try:
            return self.actions.confirm_action(self._workspace(), proposal_id)
        except ActionError as error:
            return self._error_result(error.code)

    def reject_action(self, proposal_id: str) -> dict:
        try:
            return self.actions.reject_action(self._workspace(), proposal_id)
        except ActionError as error:
            return self._error_result(error.code)

    @staticmethod
    def _error_result(code: str) -> dict:
        return {
            "ok": False,
            "status": "error",
            "id": None,
            "data": {},
            "error_code": code,
            "message": "The request could not be completed.",
        }

    def handle_text(self, text: str) -> dict:
        """Route one proactive command; ordinary/unknown text is left to chat."""
        actor, workspace = self.actor_provider(), self.workspace_provider()
        if not actor or not workspace:
            parsed = self.parser.parse(text, context={}).to_dict()
            if parsed.get("command_type") not in {None, "unknown"}:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "error",
                    "error_code": "UNAUTHORIZED",
                    "message": "Sign in with an authorized workspace to manage proactive tasks.",
                    "command": parsed,
                }
            return {"handled": False, "reason": "unauthenticated"}
        try:
            parsed = self.parser.parse(text, context=self._context(workspace)).to_dict()
        except Exception as error:
            # Still identify explicit proactive intent, then stop safely. In
            # particular, do not let a scheduled briefing fall into immediate
            # briefing execution when context storage is unavailable.
            parsed = self.parser.parse(text, context={}).to_dict()
            if parsed.get("command_type") not in {None, "unknown"}:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "error",
                    "error_code": error.code if isinstance(error, ActionError) else "STORAGE_ERROR",
                    "message": "Proactive task data could not be read. No operation was started.",
                    "command": parsed,
                }
            return {"handled": False, "reason": "read_failed"}
        kind, command = parsed.get("task_type"), parsed.get("command_type")
        if command in {None, "unknown"}:
            return {"handled": False, "reason": "not_proactive"}
        if parsed.get("missing_fields"):
            return {
                "handled": True,
                "ok": False,
                "status": "needs_clarification",
                "command": parsed,
                "message": self._clarification(parsed),
            }
        workspace_id = workspace
        if command in {"create", "update", "cancel"}:
            result = self.actions.propose_action(workspace_id, parsed)
        elif command in {"confirm", "reject"}:
            method = (
                self.actions.confirm_action if command == "confirm" else self.actions.reject_action
            )
            result = method(workspace_id, parsed["target_id"])
        elif command in {"list", "get"}:
            try:
                result = self._read(kind, command, parsed, workspace_id)
            except ActionError as error:
                result = {
                    "ok": False,
                    "status": "error",
                    "error_code": error.code,
                    "data": {},
                }
            except Exception:
                result = {
                    "ok": False,
                    "status": "error",
                    "error_code": "STORAGE_ERROR",
                    "data": {},
                }
        else:
            return {"handled": True, "ok": False, "status": "unsupported"}
        return {"handled": True, **result}

    @staticmethod
    def _clarification(command: dict) -> str:
        language = command.get("language", "en")
        fields = ", ".join(command.get("missing_fields", []))
        if language == "zh":
            return f"请补充这些信息后再继续：{fields}。目前没有创建或修改任何任务。"
        return f"Please provide: {fields}. No task has been created or changed."

    def _read(self, kind: str | None, command: str, parsed: dict, workspace: str) -> dict:
        if kind is None and command == "list":
            events = self.actions.get_action_history(workspace)
            groups: dict[str, dict[str, Any]] = {}
            for event in events:
                group = groups.setdefault(
                    event["proposal_id"],
                    {
                        "id": event["proposal_id"],
                        "action_type": event["action_type"],
                        "target_id": event["target_id"],
                        "parameters": event["parameters"],
                        "status": event["status"],
                        "result": event["result"],
                        "events": [],
                    },
                )
                group["events"].append(event)
                group["status"] = event["status"]
                group["result"] = event["result"]
            return {"ok": True, "status": "listed", "data": {"items": list(groups.values())}}

        if kind == "briefing":
            if command == "list":
                return {
                    "ok": True,
                    "status": "listed",
                    "data": {"items": self.briefings.list_schedules(workspace)},
                }
            schedule_id = parsed["target_id"]
            item = self.briefings.repository.get_schedule(workspace, schedule_id)
            if item is None:
                return {"ok": False, "status": "error", "error_code": "NOT_FOUND", "data": {}}
            return {
                "ok": True,
                "status": "found",
                "id": schedule_id,
                "data": {
                    "item": next(
                        entry
                        for entry in self.briefings.list_schedules(workspace)
                        if entry["id"] == schedule_id
                    ),
                    "history": self.briefings.list_history(workspace, schedule_id),
                },
            }
        if kind == "watch":
            if command == "list":
                return {
                    "ok": True,
                    "status": "listed",
                    "data": {
                        "items": self.watches.list_watches(workspace),
                        "alerts": self.watches.list_alerts(workspace),
                        "checks": [
                            record_data(record)
                            for record in self.watches.repository.list_checks(workspace)
                        ],
                    },
                }
            item = next(
                (
                    watch
                    for watch in self.watches.list_watches(workspace)
                    if watch["id"] == parsed["target_id"]
                ),
                None,
            )
            if item is None:
                return {"ok": False, "status": "error", "error_code": "NOT_FOUND", "data": {}}
            return {"ok": True, "status": "found", "id": item["id"], "data": {"item": item}}
        if command == "list":
            return {
                "ok": True,
                "status": "listed",
                "data": {"items": self.actions.get_pending_proposals(workspace)},
            }
        return {
            "ok": True,
            "status": "found",
            "id": parsed["target_id"],
            "data": {"proposal": self.actions.get_proposal(workspace, parsed["target_id"])},
        }


def build_proactive_application(
    *,
    llm_client: Any,
    actor_provider: Callable[[], str | None],
    workspace_provider: Callable[[], str | None],
    authorizer: Callable[..., bool],
    database: str | Path | None = None,
    orders_csv: str | Path | None = None,
) -> ProactiveApplication:
    """Build all P3/P4/P5 services with one shared, local SQLite database."""
    path = Path(database or os.getenv("PROACTIVE_DB_PATH", "runtime_data/proactive.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    action_repo = SQLiteActionRepository(path)
    briefing_repo = SQLiteBriefingRepository(path)
    watch_repo = SQLiteWatchRepository(path)
    briefings = BriefingService(briefing_repo)

    def current_workspace() -> str:
        workspace = workspace_provider()
        if not workspace:
            raise PermissionError("An authenticated workspace is required")
        return workspace

    # Bind CSV lookup to the trusted current workspace for each request.
    class WorkspaceOrderSource:
        def fetch(self, workspace_id: str, order_id: str):
            if workspace_id != current_workspace():
                raise PermissionError("Workspace mismatch")
            return OrderLookupSource(workspace_id, OrderLookupTool(orders_csv)).fetch(
                workspace_id, order_id
            )

    watches = WatchService(watch_repo, order_source=WorkspaceOrderSource())
    actions = ActionService(
        action_repo,
        {
            ("briefing", "create"): BriefingActionExecutor(briefings),
            ("briefing", "update"): BriefingActionExecutor(briefings),
            ("briefing", "cancel"): BriefingActionExecutor(briefings),
            ("watch", "create"): WatchActionExecutor(watches),
            ("watch", "cancel"): WatchActionExecutor(watches),
        },
        actor_provider=actor_provider,
        authorizer=authorizer,
    )
    return ProactiveApplication(
        parser=ProactiveCommandParser(llm_client),
        actions=actions,
        briefings=briefings,
        watches=watches,
        actor_provider=actor_provider,
        workspace_provider=workspace_provider,
    )
