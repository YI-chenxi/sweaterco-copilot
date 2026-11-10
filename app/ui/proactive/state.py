"""Pure UI state and adapters; no database, scheduler or business execution logic."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

from app.agent.contracts import AgentReply, Evidence

PAGES = ("copilot", "briefings", "watches", "actions")


def init_state(state):
    state.setdefault("ui_page", "copilot")
    if state["ui_page"] not in PAGES:
        state["ui_page"] = "copilot"
    return state.setdefault("proactive_ui", {"drafts": {}, "submissions": {}, "decisions": {}})


def bind_scope(state, application):
    """Resolve only server providers. Discard display/drafts after identity change."""
    try:
        actor = application.actor_provider() if application else None
        workspace = application.workspace_provider() if application else None
        scope = (actor, workspace) if actor and workspace else None
    except Exception:
        scope = None
    if state.get("proactive_scope") != scope:
        for key in list(state):
            if key.startswith("pa_field_") or key == "proactive_ui":
                del state[key]
        state["proactive_scope"] = scope
    init_state(state)
    return scope


def call(application, method, *args):
    """Do not expose backend exceptions or disguise an unreadable collection as empty."""
    if application is None:
        return {"ok": False, "error_code": "NOT_CONNECTED", "data": {}}
    try:
        result = getattr(application, method)(*args)
        if (
            type(result) is not dict
            or type(result.get("ok")) is not bool
            or not isinstance(result.get("data", {}), dict)
        ):
            raise ValueError()
        return result
    except Exception:
        return {"ok": False, "error_code": "STORAGE_ERROR", "data": {}}


def public_data(value):
    """Strip execution credentials before storing a UI receipt or showing parameters."""
    if isinstance(value, dict):
        return {
            k: public_data(v)
            for k, v in value.items()
            if k not in {"idempotency_key", "request_key", "fingerprint", "token", "password"}
        }
    if isinstance(value, (list, tuple)):
        return [public_data(v) for v in value]
    return deepcopy(value)


def command_digest(command):
    # Display locale/theme must never produce a new submission identity.
    business = {k: command.get(k) for k in ("command_type", "task_type", "parameters", "target_id")}
    return hashlib.sha256(json.dumps(business, sort_keys=True).encode()).hexdigest()


def propose_once(application, command, model, slot):
    digest = command_digest(command)
    previous = model["submissions"].get(slot)
    if previous and previous["state"] == "unknown":
        model["notice"] = deepcopy(previous["result"])
        return deepcopy(previous["result"])
    if previous and previous["digest"] == digest:
        identifier = previous["result"].get("id")
        current = public_data(call(application, "get_action", identifier))
        item = current.get("data", {}).get("item", {})
        if not current.get("ok"):
            model["notice"] = current
            return current
        if needs_verification(item) or (
            item.get("status") == "pending_confirmation" and item.get("execution_started_at")
        ):
            result = {"ok": False, "error_code": "RECONCILIATION_REQUIRED", "data": {}}
            model["notice"] = result
            return result
        if can_decide(item):
            result = {
                "ok": True,
                "id": identifier,
                "status": "pending_confirmation",
                "data": {"proposal": item},
            }
            previous["result"] = deepcopy(result)
            model["notice"] = result
            return result
        # A deliberate new click after a terminal request starts a NEW proposal.
        # Never reuse its old approval or its old idempotency key.
        terminal = item.get("status") in {"executed", "rejected", "expired", "failed"}
        if not terminal:
            try:
                terminal = (
                    item.get("status") == "pending_confirmation"
                    and item.get("execution_state") in {None, "not_started"}
                    and datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
                    <= datetime.now(timezone.utc)
                )
            except (KeyError, TypeError, ValueError):
                terminal = False
        if not terminal:
            result = {"ok": False, "error_code": "CONFLICT", "data": {}}
            model["notice"] = result
            return result
    result = public_data(call(application, "propose_action", command))
    code = result.get("error_code")
    state = (
        "unknown"
        if code in {"STORAGE_ERROR", "EXECUTION_ERROR"}
        else ("created" if result.get("ok") else "invalid")
    )
    # Validation errors can be corrected/resubmitted; uncertain writes cannot.
    if state != "invalid":
        model["submissions"][slot] = {"digest": digest, "state": state, "result": result}
    model["notice"] = result
    if result.get("ok") and result.get("id"):
        model["selected_action"] = result["id"]
    return result


def can_decide(proposal, *, now=None):
    if (
        proposal.get("status") != "pending_confirmation"
        or proposal.get("execution_started_at")
        or proposal.get("requires_reconciliation")
        or proposal.get("execution_state") not in {None, "not_started"}
    ):
        return False
    try:
        expiry = datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00"))
        return expiry.tzinfo is not None and expiry > (now or datetime.now(timezone.utc))
    except (ValueError, TypeError, KeyError):
        return False


def decide_once(application, proposal_id, decision, model):
    previous = model["decisions"].get(proposal_id)
    if previous is not None:
        return deepcopy(previous)
    # Reload EXACT proposal immediately before dispatch; never authorize from a stale card.
    current = call(application, "get_action", proposal_id)
    if not current.get("ok") or not can_decide(current.get("data", {}).get("item", {})):
        result = {"ok": False, "error_code": "CONFLICT", "data": {}}
    else:
        result = call(application, decision + "_action", proposal_id)
        model["decisions"][proposal_id] = public_data(result)
    model["notice"] = public_data(result)
    return result


def result_reply(result):
    """Saved briefing/observation -> existing evidence renderer, without altering source."""
    if not isinstance(result, dict):
        return None
    evidence = [
        Evidence(item["source"], item["reference"], item["detail"])
        for item in result.get("evidence", [])
        if isinstance(item, dict)
        and all(isinstance(item.get(k), str) for k in ("source", "reference", "detail"))
    ]
    return AgentReply(
        text=str(result.get("text", "")),
        evidence=evidence,
        warnings=list(result.get("warnings", [])),
        assumptions=list(result.get("assumptions", [])),
        metadata=deepcopy(result.get("metadata", {})),
    )


def action_label(item):
    kind = item.get("action_type") or ".".join(
        str(item.get("command", {}).get(k, "")) for k in ("task_type", "command_type")
    )
    return {
        "briefing.create": "Create briefing schedule",
        "briefing.update": "Update briefing schedule",
        "briefing.cancel": "Cancel briefing schedule",
        "watch.create": "Create order watch",
        "watch.cancel": "Cancel order watch",
    }.get(kind, "Action")


def action_kind(item):
    payload = item.get("command", {})
    return payload.get("task_type"), payload.get("command_type"), payload.get("parameters", {})


def confirmation_label(item):
    kind, operation, values = action_kind(item)
    if operation == "cancel":
        return "Confirm cancellation"
    if kind == "briefing" and operation == "update" and set(values) == {"enabled"}:
        return "Confirm resume" if values["enabled"] else "Confirm pause"
    return {
        ("watch", "create"): "Confirm & create watch",
        ("briefing", "create"): "Confirm & create schedule",
        ("briefing", "update"): "Confirm schedule changes",
    }.get((kind, operation), "Confirm action")


def needs_verification(item):
    return bool(
        item.get("requires_reconciliation")
        or (item.get("result") or {}).get("outcome_unknown")
        or item.get("execution_state") in {"outcome_unknown", "in_progress_or_unknown"}
    )


def outcome_copy(item):
    if needs_verification(item):
        return (
            "Outcome needs verification",
            "The outcome needs verification. Do not submit another execution.",
        )
    if item.get("status") == "rejected":
        return "Request declined", "This request was declined. No change was made by this request."
    if item.get("status") == "expired":
        return (
            "Request expired",
            "This request has expired. Review the current settings before preparing a new request.",
        )
    if item.get("status") != "executed":
        return (
            "Request was not completed",
            "Check the current status before preparing another request.",
        )
    kind, operation, values = action_kind(item)
    if operation == "cancel":
        return (
            ("Watch cancelled", "This watch has stopped checking the order.")
            if kind == "watch"
            else ("Schedule cancelled", "This schedule will no longer generate briefings.")
        )
    if kind == "briefing" and operation == "update" and set(values) == {"enabled"}:
        return (
            ("Schedule resumed", "The schedule is enabled again.")
            if values["enabled"]
            else (
                "Schedule paused",
                "No briefings will be generated by this schedule while it is paused.",
            )
        )
    return {
        ("watch", "create"): ("Watch created successfully", "Your order watch is now active."),
        ("briefing", "create"): (
            "Schedule created successfully",
            "Your briefing schedule has been saved and enabled.",
        ),
        ("briefing", "update"): ("Schedule updated", "Your schedule changes have been saved."),
    }.get((kind, operation), ("Action completed", "The change has been saved."))


def destination(item):
    kind, operation, _ = action_kind(item)
    page = "watches" if kind == "watch" else "briefings"
    receipt = (item.get("result") or {}).get("receipt") or {}
    identifier = None
    if item.get("status") == "executed" and not needs_verification(item) and receipt.get("ok"):
        identifier = receipt.get("id") or item.get("command", {}).get("target_id")
    if operation == "cancel":
        identifier = None
    return {
        "page": page,
        "section": "Active watches" if page == "watches" else "Schedules",
        "id": identifier,
        "label": ("View watch" if page == "watches" else "View schedule")
        if identifier and operation != "cancel"
        else ("Back to Watches & Alerts" if page == "watches" else "Back to Morning Briefing"),
    }


def status_label(item):
    if item.get("requires_reconciliation") or item.get("execution_state") in {
        "in_progress_or_unknown",
        "outcome_unknown",
    }:
        return "Needs verification"
    return {
        "pending_confirmation": "Awaiting confirmation",
        "executed": "Completed",
        "rejected": "Declined",
        "failed": "Failed",
        "expired": "Expired",
    }.get(item.get("status"), str(item.get("status", "Unknown")))


def command(kind, operation, parameters, *, target=None, language="en"):
    return {
        "command_type": operation,
        "task_type": kind,
        "parameters": parameters,
        "target_id": target,
        "missing_fields": [],
        "language": language,
    }
