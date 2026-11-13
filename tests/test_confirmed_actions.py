"""P4 acceptance tests use mock actions and real isolated SQLite databases."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event, Lock
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.agent.proactive.actions import ActionError, ActionService, SQLiteActionRepository

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
TARGET = "d267d559-ea0a-44cd-ae9c-13b4fab75b72"


def command(task="briefing", operation="create", **overrides):
    parameters = (
        {
            "interval_days": 1,
            "local_time": "09:00",
            "timezone": "Asia/Singapore",
            "start_date": "2026-11-04",
        }
        if task == "briefing"
        else {
            "order_id": "ORD-057",
            "condition_type": "status_equals",
            "expected_status": "DELAYED",
            "check_interval_minutes": 30,
        }
    )
    if operation == "update":
        parameters = {"interval_days": 3}
    if operation == "cancel":
        parameters = {}
    return {
        "command_type": operation,
        "task_type": task,
        "parameters": parameters,
        "target_id": None if operation == "create" else TARGET,
        "missing_fields": [],
        "language": "en",
        **overrides,
    }


class FakeExecutor:
    """Contract-compliant fake P3/P5 boundary, never a production business service."""

    def __init__(self):
        self.calls = []
        self.version = "revision-1"
        self.failure = None
        self.validation_error = None
        self.receipt = None
        self.started = None
        self.release = None
        self.lock = Lock()
        self.keys = set()

    def validate(self, workspace_id, cmd):
        if self.validation_error:
            raise ActionError(self.validation_error)
        if cmd["command_type"] == "create" and cmd["task_type"] == "briefing":
            if not {"interval_days", "local_time", "start_date"} <= cmd["parameters"].keys():
                raise ActionError("MISSING_FIELD")
            interval = cmd["parameters"]["interval_days"]
            if type(interval) is not int or interval <= 0:
                raise ActionError("INVALID_ARGUMENT")
        return None if cmd["command_type"] == "create" else self.version

    def execute(self, workspace_id, cmd, *, idempotency_key, expected_version):
        with self.lock:
            if expected_version is not None and expected_version != self.version:
                raise ActionError("CONFLICT")
            assert idempotency_key not in self.keys
            self.keys.add(idempotency_key)
            self.calls.append((workspace_id, deepcopy(cmd), idempotency_key))
        if self.started:
            self.started.set()
            assert self.release.wait(10), "test release was not signalled"
        if self.failure:
            raise self.failure
        return self.receipt or {
            "ok": True,
            "status": "completed",
            "id": TARGET,
            "data": {"target_id": TARGET},
            "error_code": None,
            "message": "not persisted",
        }


def service(repo, executor, context, **kwargs):
    return ActionService(
        repo,
        {
            (task, operation): executor
            for task, operations in (
                ("briefing", ("create", "update", "cancel")),
                ("watch", ("create", "cancel")),
            )
            for operation in operations
        },
        actor_provider=lambda: context.actor,
        authorizer=lambda actor, workspace, operation, proposal: (
            actor == "operator" and workspace == "group02" and context.allowed
        ),
        clock=lambda: context.now,
        **kwargs,
    )


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "actions.sqlite3"
    repo = SQLiteActionRepository(path)
    executor = FakeExecutor()
    context = SimpleNamespace(actor="operator", allowed=True, now=NOW)
    result = SimpleNamespace(repo=repo, executor=executor, context=context, path=path)
    result.service = service(repo, executor, context)
    yield result
    repo.close()


def propose(env, cmd=None):
    result = env.service.propose_action("group02", cmd or command())
    assert result["ok"], result
    return result


@pytest.mark.parametrize(
    "task,operation",
    [
        ("briefing", "create"),
        ("briefing", "update"),
        ("briefing", "cancel"),
        ("watch", "create"),
        ("watch", "cancel"),
    ],
)
def test_proposal_has_no_side_effect_until_confirmed(env, task, operation):
    result = propose(env, command(task, operation))
    proposal = result["data"]["proposal"]
    assert proposal["status"] == "pending_confirmation"
    assert UUID(proposal["id"]) and UUID(proposal["idempotency_key"])
    assert proposal["workspace_id"] == "group02" and proposal["creator_id"] == "operator"
    assert proposal["expires_at"] == "2026-11-03T09:15:00.000000Z"
    assert env.executor.calls == []
    first = env.service.confirm_action("group02", result["id"])
    second = env.service.confirm_action("group02", result["id"])
    assert first["ok"] and first["status"] == "executed"
    assert second["ok"] and second["data"]["already_executed"]
    assert len(env.executor.calls) == 1
    records = env.service.get_action_history("group02")
    assert [item["event"] for item in records] == ["proposed", "execution_started", "executed"]
    assert records[-1]["result"]["receipt"]["data"]["target_id"] == TARGET
    assert "message" not in records[-1]["result"]["receipt"]


def test_no_identity_or_no_permission_defaults_to_denied(env):
    without_identity = ActionService(env.repo, env.service.executors)
    assert without_identity.propose_action("group02", command())["error_code"] == "UNAUTHORIZED"
    without_policy = ActionService(
        env.repo, env.service.executors, actor_provider=lambda: "operator"
    )
    assert without_policy.propose_action("group02", command())["error_code"] == "UNAUTHORIZED"
    assert env.repo.history("group02") == []


@pytest.mark.parametrize("operation", ["confirm", "reject", "history"])
def test_revoked_permission_denies_access_without_mutation(env, operation):
    pid = propose(env)["id"]
    env.context.allowed = False
    if operation == "history":
        with pytest.raises(ActionError, match="UNAUTHORIZED"):
            env.service.get_action_history("group02")
    else:
        fn = env.service.confirm_action if operation == "confirm" else env.service.reject_action
        assert fn("group02", pid)["error_code"] == "UNAUTHORIZED"
    assert env.repo.get("group02", pid)["status"] == "pending_confirmation"
    assert env.executor.calls == []


def test_other_creator_cannot_confirm_or_reject_even_with_workspace_permission(env):
    pid = propose(env)["id"]
    env.context.actor = "different-operator"
    env.service.authorizer = lambda *_args: True
    assert env.service.confirm_action("group02", pid)["error_code"] == "UNAUTHORIZED"
    assert env.service.reject_action("group02", pid)["error_code"] == "UNAUTHORIZED"
    assert not env.executor.calls


def test_workspace_isolation_and_unknown_proposal(env):
    pid = propose(env)["id"]
    env.service.authorizer = lambda *_args: True
    assert env.service.confirm_action("other-workspace", pid)["error_code"] == "NOT_FOUND"
    assert env.service.reject_action("other-workspace", pid)["error_code"] == "NOT_FOUND"
    assert env.service.get_action_history("other-workspace") == []
    assert env.service.confirm_action("group02", str(uuid4()))["error_code"] == "NOT_FOUND"
    assert len(env.service.get_action_history("group02")) == 1


@pytest.mark.parametrize("bad_id", [None, "", "yes", "确认", " ", 123, [], {}])
def test_confirmation_requires_exact_uuid(env, bad_id):
    propose(env)
    assert env.service.confirm_action("group02", bad_id)["error_code"] == "INVALID_ARGUMENT"
    assert not env.executor.calls


def test_reject_is_idempotent_and_cannot_be_confirmed(env):
    pid = propose(env)["id"]
    assert env.service.reject_action("group02", pid)["status"] == "rejected"
    assert env.service.reject_action("group02", pid)["ok"]
    assert env.service.confirm_action("group02", pid)["error_code"] == "CONFLICT"
    assert [e["event"] for e in env.repo.history("group02")] == ["proposed", "rejected"]
    assert not env.executor.calls


def test_executed_action_cannot_be_rejected(env):
    pid = propose(env)["id"]
    env.service.confirm_action("group02", pid)
    assert env.service.reject_action("group02", pid)["error_code"] == "ALREADY_EXECUTED"
    assert env.repo.get("group02", pid)["status"] == "executed"


@pytest.mark.parametrize("operation", ["confirm", "reject"])
def test_expiry_at_exact_boundary_is_recorded_once(env, operation):
    pid = propose(env)["id"]
    env.context.now += timedelta(minutes=15)
    fn = env.service.confirm_action if operation == "confirm" else env.service.reject_action
    assert fn("group02", pid)["error_code"] == "EXPIRED"
    assert fn("group02", pid)["error_code"] == "EXPIRED"
    assert [e["event"] for e in env.repo.history("group02")] == ["proposed", "expired"]
    assert not env.executor.calls


def test_clock_normalizes_real_scheduling_time_not_factory_snapshot_date(env):
    env.context.now = NOW.astimezone(timezone(timedelta(hours=8)))
    result = propose(env)
    assert result["data"]["proposal"]["created_at"] == "2026-11-03T09:00:00.000000Z"
    env.context.now = NOW.replace(tzinfo=None)
    assert env.service.confirm_action("group02", result["id"])["error_code"] == "INVALID_ARGUMENT"
    assert not env.executor.calls


def test_target_changed_since_proposal_is_not_executed(env):
    pid = propose(env, command(operation="update"))["id"]
    env.executor.version = "revision-2"
    result = env.service.confirm_action("group02", pid)
    assert result["status"] == "failed" and result["error_code"] == "CONFLICT"
    assert not env.executor.calls


def test_change_between_revalidation_and_write_is_caught_by_executor(env, monkeypatch):
    pid = propose(env, command(operation="cancel"))["id"]
    old_validate = env.executor.validate

    def raced_validate(workspace, cmd):
        version = old_validate(workspace, cmd)
        env.executor.version = "revision-2"
        return version

    monkeypatch.setattr(env.executor, "validate", raced_validate)
    assert env.service.confirm_action("group02", pid)["error_code"] == "CONFLICT"
    assert not env.executor.calls


@pytest.mark.parametrize(
    "code", ["NOT_FOUND", "CONFLICT", "UNSUPPORTED_CONDITION", "MISSING_FIELD"]
)
def test_domain_service_rechecks_target_and_fields_before_execution(env, code):
    pid = propose(env)["id"]
    env.executor.validation_error = code
    assert env.service.confirm_action("group02", pid)["error_code"] == code
    assert not env.executor.calls


def test_permission_is_checked_again_immediately_before_write(env, monkeypatch):
    pid = propose(env)["id"]
    original = env.executor.validate

    def revoke(workspace, cmd):
        env.context.allowed = False
        return original(workspace, cmd)

    monkeypatch.setattr(env.executor, "validate", revoke)
    assert env.service.confirm_action("group02", pid)["error_code"] == "UNAUTHORIZED"
    assert not env.executor.calls


def test_proposal_expiring_during_validation_never_executes(env, monkeypatch):
    pid = propose(env)["id"]
    original = env.executor.validate

    def expire(workspace, cmd):
        env.context.now += timedelta(hours=1)
        return original(workspace, cmd)

    monkeypatch.setattr(env.executor, "validate", expire)
    result = env.service.confirm_action("group02", pid)
    assert result["status"] == "expired" and result["error_code"] == "EXPIRED"
    assert not env.executor.calls


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"task_type": []},
        command(command_type="list"),
        command(command_type="confirm"),
        command(task_type="unknown"),
        command(missing_fields=["local_time"]),
        command(missing_fields="local_time"),
        command(language="fr"),
        command(parameters={"api_key": "PRIVATE_DETAIL"}),
        command(parameters={}),
        command(parameters={"interval_days": None}),
        command(target_id=TARGET),
        command(operation="cancel", target_id=None),
        command(operation="update", parameters={}),
        command(operation="cancel", parameters={"enabled": False}),
        command(actor_id="operator"),
        command(parameters={"interval_days": float("nan")}),
        command(
            parameters={"interval_days": True, "local_time": "09:00", "start_date": "2026-11-04"}
        ),
    ],
)
def test_invalid_commands_never_become_executable_proposals(env, bad):
    result = env.service.propose_action("group02", bad)
    assert not result["ok"]
    assert result["error_code"] in {"INVALID_ARGUMENT", "MISSING_FIELD"}
    assert env.repo.history("group02") == [] and not env.executor.calls


def test_unregistered_watch_update_is_explicitly_unsupported(env):
    result = env.service.propose_action(
        "group02", command("watch", "update", parameters={"check_interval_minutes": 60})
    )
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert not env.executor.calls


def test_domain_missing_field_rejected_even_if_parser_reports_complete(env):
    result = env.service.propose_action("group02", command(parameters={"interval_days": 1}))
    assert result["error_code"] == "MISSING_FIELD"


def test_reference_text_is_not_persisted_and_payloads_are_detached(env):
    cmd = command(reference_text="DO_NOT_STORE_RAW_REQUEST")
    result = propose(env, cmd)
    pid = result["id"]
    cmd["parameters"]["interval_days"] = 99
    result["data"]["proposal"]["command"]["parameters"]["interval_days"] = 88
    records = env.service.get_action_history("group02")
    records[0]["parameters"]["interval_days"] = 77
    assert env.repo.get("group02", pid)["command"]["parameters"]["interval_days"] == 1
    assert "DO_NOT_STORE_RAW_REQUEST" not in json.dumps(env.repo.get("group02", pid))
    assert "DO_NOT_STORE_RAW_REQUEST" not in json.dumps(env.repo.history("group02"))


def test_server_request_key_deduplicates_propose_and_rejects_payload_reuse(env):
    env.service.request_key_provider = lambda: "trusted-turn-123"
    first, second = propose(env), propose(env)
    assert first["id"] == second["id"]
    changed = command()
    changed["parameters"]["interval_days"] = 3
    assert env.service.propose_action("group02", changed)["error_code"] == "CONFLICT"
    assert len(env.repo.history("group02")) == 1


def test_new_explicit_request_may_repeat_same_business_action(env):
    first, second = propose(env), propose(env)
    assert first["id"] != second["id"]  # No fuzzy natural-language command deduplication.


def test_error_and_exception_text_never_leaks_and_failed_action_is_not_retried(env):
    pid = propose(env)["id"]
    env.executor.failure = RuntimeError("PRIVATE_PROVIDER_DETAIL")
    result = env.service.confirm_action("group02", pid)
    assert result["status"] == "failed" and result["error_code"] == "EXECUTION_ERROR"
    assert result["data"]["proposal"]["requires_reconciliation"]
    env.service.confirm_action("group02", pid)
    assert len(env.executor.calls) == 1
    assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps(result)
    assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps(env.repo.history("group02"))


def test_definite_business_failure_is_recorded(env):
    env.executor.receipt = {
        "ok": False,
        "status": "not_found",
        "id": None,
        "data": {},
        "error_code": "NOT_FOUND",
        "message": "PRIVATE_PROVIDER_DETAIL",
    }
    pid = propose(env)["id"]
    result = env.service.confirm_action("group02", pid)
    assert result["status"] == "failed" and result["error_code"] == "NOT_FOUND"
    assert not result["data"]["proposal"]["requires_reconciliation"]
    assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps(env.repo.history("group02"))


@pytest.mark.parametrize(
    "receipt",
    [
        {"ok": "yes"},
        {"ok": True, "status": "ok", "data": {}, "error_code": "NOT_FOUND"},
        {"ok": True, "status": "ok", "data": {"bad": object()}},
    ],
)
def test_malformed_executor_result_is_not_reported_as_success(env, receipt):
    env.executor.receipt = receipt
    pid = propose(env)["id"]
    result = env.service.confirm_action("group02", pid)
    assert result["error_code"] == "EXECUTION_ERROR"
    assert result["data"]["proposal"]["requires_reconciliation"]


def test_records_survive_repository_restart_and_success_is_not_reexecuted(env):
    first = propose(env)
    env.repo.close()
    with SQLiteActionRepository(env.path) as reopened:
        resumed = service(reopened, env.executor, env.context)
        assert resumed.confirm_action("group02", first["id"])["ok"]
    with SQLiteActionRepository(env.path) as reopened:
        resumed = service(reopened, env.executor, env.context)
        assert resumed.confirm_action("group02", first["id"])["data"]["already_executed"]
        assert len(resumed.get_action_history("group02")) == 3
    assert len(env.executor.calls) == 1


def test_crash_after_claim_requires_reconciliation_and_never_blindly_retries(env):
    pid = propose(env)["id"]
    env.repo.claim("group02", pid, "operator", "2026-11-03T09:00:00.000000Z")
    env.repo.close()
    env.context.now += timedelta(days=1)
    with SQLiteActionRepository(env.path) as reopened:
        resumed = service(reopened, env.executor, env.context)
        result = resumed.confirm_action("group02", pid)
        assert result["error_code"] == "CONFLICT"
        assert result["data"]["proposal"]["requires_reconciliation"]
        assert resumed.reject_action("group02", pid)["error_code"] == "CONFLICT"
    assert not env.executor.calls


def test_concurrent_confirmations_across_connections_invoke_executor_once(env):
    pid = propose(env)["id"]
    started, release = Event(), Event()
    env.executor.started, env.executor.release = started, release
    with SQLiteActionRepository(env.path) as other_repo:
        other = service(other_repo, env.executor, env.context)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(env.service.confirm_action, "group02", pid)
            try:
                assert started.wait(10)
                second = pool.submit(other.confirm_action, "group02", pid)
                assert second.result(timeout=10)["error_code"] == "CONFLICT"
                assert other.reject_action("group02", pid)["error_code"] == "CONFLICT"
            finally:
                release.set()
            assert first.result(timeout=10)["ok"]
    assert len(env.executor.calls) == 1
    assert [e["event"] for e in env.repo.history("group02")] == [
        "proposed",
        "execution_started",
        "executed",
    ]


def test_proposal_and_audit_are_one_transaction(env, monkeypatch):
    def broken_audit(*_args):
        raise sqlite3.OperationalError("PRIVATE_DATABASE_DETAIL")

    monkeypatch.setattr(env.repo, "_audit", broken_audit)
    env.service.request_key_provider = lambda: "request-1"
    result = env.service.propose_action("group02", command())
    assert result["error_code"] == "STORAGE_ERROR"
    with sqlite3.connect(env.path) as connection:
        assert connection.execute("SELECT count(*) FROM action_proposals").fetchone()[0] == 0
    assert "PRIVATE_DATABASE_DETAIL" not in json.dumps(result)


def test_claim_audit_failure_rolls_back_and_no_executor_is_called(env, monkeypatch):
    pid = propose(env)["id"]
    with monkeypatch.context() as patch:
        patch.setattr(env.repo, "_audit", lambda *_args: (_ for _ in ()).throw(RuntimeError()))
        assert env.service.confirm_action("group02", pid)["error_code"] == "STORAGE_ERROR"
    assert not env.executor.calls
    assert env.repo.get("group02", pid)["execution_started_at"] is None
    assert env.service.confirm_action("group02", pid)["ok"]


def test_result_storage_failure_leaves_durable_claim_and_blocks_reexecution(env, monkeypatch):
    pid = propose(env)["id"]
    original = env.repo._audit

    def fail_finish(proposal, event, actor, at):
        if event == "executed":
            raise sqlite3.OperationalError("simulated disk failure")
        return original(proposal, event, actor, at)

    with monkeypatch.context() as patch:
        patch.setattr(env.repo, "_audit", fail_finish)
        assert env.service.confirm_action("group02", pid)["error_code"] == "STORAGE_ERROR"
    assert env.repo.get("group02", pid)["status"] == "pending_confirmation"
    assert env.service.confirm_action("group02", pid)["error_code"] == "CONFLICT"
    assert len(env.executor.calls) == 1


def test_history_denial_and_storage_failure_are_not_empty_success(env):
    env.repo.close()
    with pytest.raises(ActionError, match="STORAGE_ERROR"):
        env.service.get_action_history("group02")


def test_chinese_command_returns_localized_confirmation_and_success(env):
    result = propose(env, command(language="zh"))
    assert "确认" in result["message"]
    assert "已执行" in env.service.confirm_action("group02", result["id"])["message"]


def test_auth_callback_receives_detached_proposal(env):
    pid = propose(env)["id"]

    def policy(_actor, _workspace, _operation, proposal):
        proposal["command"]["parameters"]["interval_days"] = 999
        return True

    env.service.authorizer = policy
    assert env.service.confirm_action("group02", pid)["ok"]
    assert env.executor.calls[0][1]["parameters"]["interval_days"] == 1


def test_large_valid_receipt_does_not_break_proposal_replay_or_authorization(env):
    # Each command/receipt is bounded, but their combined stored envelope may be larger.
    raw = command()
    raw["parameters"]["timezone"] = "x" * 36000
    pid = propose(env, raw)["id"]
    env.executor.receipt = {
        "ok": True,
        "status": "created",
        "id": TARGET,
        "data": {"business_detail": "y" * 36000},
        "error_code": None,
    }
    assert env.service.confirm_action("group02", pid)["ok"]
    assert env.service.confirm_action("group02", pid)["data"]["already_executed"]
    assert len(env.executor.calls) == 1


def test_lost_claim_acknowledgement_blocks_execution_and_requires_reconciliation(env, monkeypatch):
    pid = propose(env)["id"]
    original = env.repo.claim

    def claim_then_disconnect(*args):
        original(*args)
        raise ConnectionError("PRIVATE_STORAGE_DETAIL")

    with monkeypatch.context() as patch:
        patch.setattr(env.repo, "claim", claim_then_disconnect)
        result = env.service.confirm_action("group02", pid)
        assert result["error_code"] == "STORAGE_ERROR"
    assert env.service.confirm_action("group02", pid)["error_code"] == "CONFLICT"
    assert not env.executor.calls


def test_concurrent_proposal_retries_share_one_record(env):
    env.service.request_key_provider = lambda: "same-server-turn"
    with SQLiteActionRepository(env.path) as other_repo:
        other = service(
            other_repo,
            env.executor,
            env.context,
            request_key_provider=lambda: "same-server-turn",
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda svc: svc.propose_action("group02", command()), [env.service, other])
            )
    assert all(result["ok"] for result in results)
    assert results[0]["id"] == results[1]["id"]
    assert len(env.repo.history("group02")) == 1


def test_pending_proposal_survives_a_separate_python_process(env):
    import subprocess
    import sys

    script = """
from app.agent.proactive.actions import ActionService, SQLiteActionRepository
from datetime import datetime, timezone
import json, sys
class Executor:
    def validate(self, workspace, command):
        return None
    def execute(self, *args, **kwargs):
        raise AssertionError("Must not execute during proposal creation")
with SQLiteActionRepository(sys.argv[1]) as repo:
    service = ActionService(repo, {("briefing", "create"): Executor()},
        actor_provider=lambda: "operator", authorizer=lambda *args: True,
        clock=lambda: datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc))
    result = service.propose_action("group02", json.loads(sys.argv[2]))
    print(json.dumps(result))
"""
    process = subprocess.run(
        [sys.executable, "-B", "-c", script, str(env.path), json.dumps(command())],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    pid = json.loads(process.stdout)["id"]
    assert env.repo.get("group02", pid)["status"] == "pending_confirmation"
    assert env.service.confirm_action("group02", pid)["ok"]
    assert len(env.executor.calls) == 1


def test_no_revision_for_existing_target_fails_closed(env, monkeypatch):
    monkeypatch.setattr(env.executor, "validate", lambda *_args: None)
    result = env.service.propose_action("group02", command(operation="cancel"))
    assert result["error_code"] == "CONFLICT"
    assert not env.executor.calls


def test_oversized_command_is_rejected_without_storing_any_record(env):
    raw = command()
    raw["parameters"]["timezone"] = "x" * 66000
    assert env.service.propose_action("group02", raw)["error_code"] == "INVALID_ARGUMENT"
    assert env.repo.history("group02") == []
