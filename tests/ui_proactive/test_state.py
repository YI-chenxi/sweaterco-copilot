from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ui.proactive.state import (
    bind_scope,
    call,
    can_decide,
    command,
    decide_once,
    init_state,
    propose_once,
    public_data,
)


def proposal():
    return {
        "id": "proposal",
        "status": "pending_confirmation",
        "execution_state": "not_started",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }


def test_duplicate_submit_and_theme_locale_rerun_do_not_create_another_proposal():
    calls = []
    pending = {**proposal(), "idempotency_key": "PRIVATE"}
    application = SimpleNamespace(
        get_action=lambda identifier: {"ok": True, "data": {"item": pending}},
        propose_action=lambda data: (
            calls.append(data)
            or {
                "ok": True,
                "status": "pending_confirmation",
                "id": "proposal",
                "data": {"proposal": pending},
            }
        ),
    )
    state = {}
    model = init_state(state)
    payload = command("watch", "create", {"order_id": "ORD-057"})
    first = propose_once(application, payload, model, "form")
    translated = {**payload, "language": "zh"}
    second = propose_once(application, translated, model, "form")
    assert len(calls) == 1 and first == second
    assert "PRIVATE" not in str(model) and "idempotency_key" not in str(model)


def test_uncertain_submission_blocks_changed_fields_and_new_digest():
    calls = []

    def unknown(payload):
        calls.append(payload)
        raise RuntimeError("private database details")

    application = SimpleNamespace(propose_action=unknown)
    model = init_state({})
    propose_once(application, command("watch", "create", {"order_id": "ORD-057"}), model, "form")
    result = propose_once(
        application, command("watch", "create", {"order_id": "ORD-058"}), model, "form"
    )
    assert len(calls) == 1 and not result["ok"]
    assert "private database" not in str(model)


@pytest.mark.parametrize("status", ["executed", "rejected", "expired", "failed"])
def test_explicit_resubmission_after_terminal_status_creates_a_new_proposal(status):
    calls = []
    current = proposal()

    def create(payload):
        calls.append(payload)
        return {"ok": True, "id": str(len(calls)), "data": {"proposal": proposal()}}

    application = SimpleNamespace(
        propose_action=create,
        get_action=lambda identifier: {"ok": True, "data": {"item": current}},
    )
    model = init_state({})
    payload = command("briefing", "update", {"enabled": False}, target="schedule")
    first = propose_once(application, payload, model, "pause")
    current["status"] = status
    second = propose_once(application, payload, model, "pause")
    assert first["id"] != second["id"] and len(calls) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed", "requires_reconciliation": True},
        {"status": "executed", "result": {"outcome_unknown": True}},
        {"execution_started_at": "2026-11-10T00:00:00Z"},
    ],
)
def test_uncertain_live_status_blocks_resubmission(changes):
    calls = []
    application = SimpleNamespace(
        propose_action=lambda payload: (
            calls.append(payload) or {"ok": True, "id": "original", "data": {}}
        ),
        get_action=lambda identifier: {"ok": True, "data": {"item": {**proposal(), **changes}}},
    )
    model = init_state({})
    payload = command("watch", "cancel", {}, target="watch")
    propose_once(application, payload, model, "cancel")
    result = propose_once(application, payload, model, "cancel")
    assert result["error_code"] == "RECONCILIATION_REQUIRED" and len(calls) == 1


def test_unreadable_prior_request_does_not_allow_a_new_write():
    calls = []
    application = SimpleNamespace(
        propose_action=lambda payload: (
            calls.append(payload) or {"ok": True, "id": "original", "data": {}}
        ),
        get_action=lambda identifier: {"ok": False, "error_code": "STORAGE_ERROR", "data": {}},
    )
    model = init_state({})
    payload = command("watch", "create", {"order_id": "ORD-057"})
    propose_once(application, payload, model, "create")
    result = propose_once(application, payload, model, "create")
    assert result["error_code"] == "STORAGE_ERROR" and len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "executed"},
        {"status": "rejected"},
        {"requires_reconciliation": True},
        {"execution_state": "outcome_unknown"},
        {"execution_started_at": "2026-11-08"},
        {"expires_at": "invalid"},
        {"expires_at": "2020-01-01T00:00:00+00:00"},
    ],
)
def test_non_actionable_proposal_never_shows_an_enabled_confirm(changes):
    assert not can_decide({**proposal(), **changes})


def test_decision_reloads_exact_proposal_and_is_not_repeated():
    calls = []
    application = SimpleNamespace(
        get_action=lambda identifier: {"ok": True, "data": {"item": proposal()}},
        confirm_action=lambda identifier: (
            calls.append(identifier) or {"ok": True, "status": "executed", "data": {}}
        ),
    )
    model = init_state({})
    decide_once(application, "proposal", "confirm", model)
    decide_once(application, "proposal", "confirm", model)
    assert calls == ["proposal"]


def test_stale_pending_card_does_not_authorize_confirmation():
    calls = []
    application = SimpleNamespace(
        get_action=lambda identifier: {
            "ok": True,
            "data": {"item": {**proposal(), "status": "executed"}},
        },
        confirm_action=lambda identifier: calls.append(identifier),
    )
    result = decide_once(application, "proposal", "confirm", init_state({}))
    assert not result["ok"] and not calls


def test_changing_verified_scope_discards_old_drafts_receipts_and_selected_evidence():
    application = SimpleNamespace(actor_provider=lambda: "A", workspace_provider=lambda: "w")
    state = {"threads": [{"messages": ["existing chat"]}]}
    bind_scope(state, application)
    model = state["proactive_ui"]
    model["drafts"]["order"] = "ORD-057"
    model["selection"] = {"watches": ("check", "record")}
    state["pa_field_order"] = "ORD-057"
    application.actor_provider = lambda: "B"
    bind_scope(state, application)
    assert state["proactive_ui"]["drafts"] == {} and "pa_field_order" not in state
    assert state["threads"][0]["messages"] == ["existing chat"]


def test_read_failure_does_not_become_an_empty_successful_list():
    def broken():
        raise RuntimeError("secret")

    result = call(SimpleNamespace(list_watches=broken), "list_watches")
    assert result["ok"] is False and result["error_code"] == "STORAGE_ERROR"
    assert "secret" not in str(result)


def test_public_display_copy_cannot_modify_backend_records():
    original = {"data": {"idempotency_key": "PRIVATE", "parameters": {"enabled": True}}}
    copied = public_data(original)
    copied["data"]["parameters"]["enabled"] = False
    assert original == {"data": {"idempotency_key": "PRIVATE", "parameters": {"enabled": True}}}
