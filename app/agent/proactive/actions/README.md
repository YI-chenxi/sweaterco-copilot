# P4 confirmed actions

Independent P4 implementation on branch feat/confirmed-actions, based on
fec149e from integration/intent-enhancement (3 November 2026).
The existing four workflows, shared contracts, Agent entry points and UI are unchanged.

This module persists proposals and audit events, requires explicit confirmation,
checks server-side permissions, and claims each proposal durably before invoking
an injected business executor. It does not implement the P1 parser, P3 briefing
scheduler, P5 watches, public login, cloud database or UI confirmation controls.

## Files and public API

- service.py: ActionService and the four contract methods.
- models.py: local ActionRepository, ActionExecutor, Authorizer protocols;
  safe ActionError and JSON input validation.
- repository.py: SQLiteActionRepository, atomic proposal/claim/result/audit storage.
- demo.py: offline simulation; does not create real schedules or watches.
- tests/test_confirmed_actions.py: independent acceptance tests.
- HANDOFF.md: Chinese integration notes.

The public signatures remain:

~~~python
propose_action(workspace_id, command) -> dict
confirm_action(workspace_id, proposal_id) -> dict
reject_action(workspace_id, proposal_id) -> dict
get_action_history(workspace_id) -> list[dict]
~~~

The first three return the documented ServiceResult envelope:
ok, status, id, data, error_code, message.
data.proposal contains the saved action and its current state.
Repeated successful confirmation returns the saved result with
data.already_executed = true; it does not invoke the executor again.
History returns ordered audit events and raises safe ActionError on authorization
or storage failure. It never disguises a failure as an empty successful list.

## Try it without an API key

From the repository root, with the project's Python environment:

~~~powershell
python -m app.agent.proactive.actions.demo
python -m pytest -q tests/test_confirmed_actions.py
python -m pytest -q
python -m ruff check app/agent/proactive/actions tests/test_confirmed_actions.py
python -m ruff format --check app/agent/proactive/actions tests/test_confirmed_actions.py
git diff --check
~~~

The demo prints simulation: true, a successful confirmation, an idempotent
repeat, a rejection and business_calls: 1. The default repository is in memory.
For a persistent local demonstration, explicitly pass
--database runtime_data/confirmed_actions_demo.sqlite3.
The factory services remain mocked, even when the proposal/audit database is a file.

## Wiring a service

~~~python
from datetime import timedelta
from app.agent.proactive.actions import ActionService, SQLiteActionRepository

repository = SQLiteActionRepository("runtime_data/actions.sqlite3")
actions = ActionService(
    repository,
    executors={
        ("briefing", "create"): briefing_create_adapter,
        ("briefing", "update"): briefing_update_adapter,
        ("briefing", "cancel"): briefing_cancel_adapter,
        ("watch", "create"): watch_create_adapter,
        ("watch", "cancel"): watch_cancel_adapter,
    },
    actor_provider=current_authenticated_actor_id,
    authorizer=server_permission_policy,
    proposal_ttl=timedelta(minutes=15),
    request_key_provider=current_server_request_id,
)
~~~

Names ending in adapter/policy/provider above are integration dependencies to be
supplied, not imports of unfinished branches. Ensure the database parent directory
exists. Close the repository when its owning service shuts down. Python 3.10+
and the standard library suffice; no extra package, model client or secrets are used.

### Identity and authorization

The actor provider must return a trusted authenticated server identity for the
current request; never use actor/workspace values claimed by the LLM as proof of
permission. Missing identity or policy denies access. The policy receives
(actor_id, workspace_id, operation, proposal_or_none) and must return exactly
True to allow propose, confirm, reject or history.

Confirmation/rejection additionally require the proposal's original creator.
Workspace membership alone does not allow another operator to approve it.
The policy is checked on confirmation and again immediately before the business
write. Pass a request-scoped identity provider; do not share a fixed logged-in
user across anonymous Streamlit sessions. Real authentication is an integration task.

A proposal is scoped to workspace_id; wrong-workspace lookups return NOT_FOUND.
History is read-only and scoped to the authorized workspace, including its other
operators' records when the policy explicitly allows workspace history access.

### Commands and business validation

P4 accepts only normalized create, update and cancel commands for
briefing or watch. list/get are routed directly to the relevant read service;
confirm/reject must carry one exact proposal UUID into the matching P4 method.
An unbound "yes" is never resolved by this module.

Create has no target ID; update/cancel require a UUID target ID.
Unknown fields, unknown parameter names, missing fields, non-JSON values,
non-finite numbers and payloads over 64 KiB are rejected.
Cancel takes no parameters; update/create need a non-empty parameter dictionary.
P1's missing_fields blocks proposal creation.

P3/P5 own detailed domain validation (valid times, timezones, dates, intervals,
order existence and supported watch conditions). The executor must validate them
even if P1 reports no missing fields. P4 does not reuse the April factory snapshot
date as the confirmation clock.

reference_text is accepted for compatibility but deliberately not persisted.
Identity fields cannot be injected through the command. The service never returns
or audits raw exception text. Executor receipts omit their free-text message;
their structured business data must not contain credentials or private debug data.

### Executor boundary and the integration gap

Each registered adapter implements:

~~~python
def validate(workspace_id: str, command: dict) -> str | None:
    # Read only; validate all fields and target state.
    # Return target revision for update/cancel. Create may return None.
    ...


def execute(
    workspace_id: str,
    command: dict,
    *,
    idempotency_key: str,
    expected_version: str | None,
) -> dict:
    # Atomically compare revision and mutate; persist the execution key.
    # Return the documented ServiceResult.
    ...
~~~

validate runs at proposal creation and again at confirmation. If the revision
changes, confirmation fails without invoking the write. The executor must also
compare the revision atomically with its mutation to close the validation/write race.
An update/cancel adapter returning no revision is rejected.

The documented P3/P5 public methods do not yet expose revision checks or
idempotency keys. Integration must implement these in their repository adapters
(or a shared unit of work) before wrapping those methods as production executors.
Simply wrapping a non-idempotent service call does not provide that guarantee.
No shared contract or another person's implementation is changed by this PR.

P5's V1 contract has no update_watch method. Do not register a watch/update
executor until its owner supplies and agrees an implementation. Unregistered
operations fail explicitly with INVALID_ARGUMENT.

### State and expiry

The persisted public states remain
pending_confirmation, executed, failed, rejected, expired.

A pending proposal also has an internal durable execution_started_at claim.
This is not a new shared state enum. execution_started in the audit means
the proposal was claimed, not proof that a business mutation has occurred.
A proposal already claimed cannot be rejected or claimed by a second worker.

UTC-aware ISO timestamps and UUID proposal/audit/idempotency identifiers are used.
The default configurable TTL is 15 minutes; the team can change that policy.
At now == expires_at execution is forbidden. Expiry is recorded lazily when a
pending proposal is confirmed/rejected, including a second check after validation.
History itself never changes state; an untouched expired proposal may therefore
have only its original proposed audit event. The UI can compare expires_at
with the current time. Singapore formatting belongs to the frontend.

New parameter choices require a new proposal and confirmation. Existing proposals
are immutable; they cannot be silently edited after the user reviewed them.
Automatic executions of an already confirmed recurring schedule do not need a new
user confirmation for every scheduled occurrence.

## Idempotency, transactions and recovery

SQLite mutations use BEGIN IMMEDIATE, a busy timeout and FULL synchronous mode.
The proposal state and corresponding audit event commit in one transaction.
The audit API is append-only; it is not a tamper-proof compliance ledger.

Two connections/processes sharing the same database cannot both claim a proposal.
A successful business call followed by an audit/result-storage failure leaves the
durable claim. Repeated confirmation returns CONFLICT rather than calling again.
A crash after claiming but before writing also leaves that claim.

Consequently, P4 guarantees at most one executor invocation for a proposal in this
database. It does NOT claim unconditional exactly-once effects across separate
databases/network services. The downstream adapter's durable idempotency key and
atomic business write are essential for that stronger outcome.

Unexpected executor errors or malformed receipts set
data.proposal.requires_reconciliation = true. Claimed proposals with no terminal
result also carry that flag and execution_state = in_progress_or_unknown.
Do not auto-unlock, retry or create a replacement action until the downstream
idempotency receipt/target state is reconciled. This version has no administrative
recovery endpoint; integration must supply a reviewed recovery process.

An optional trusted request_key_provider deduplicates proposal submission retries
using (workspace_id, creator_id, request_key). Reusing a key with a different
normalized command fails with CONFLICT. The key must stay stable across retries
and must not be invented by the parser. If no key is provided, each deliberate
propose call creates a new proposal; matching natural-language text is not used
to deduplicate independent user requests.

## Persistence and deployment limits

SQLite files survive reopening the database/process in the same persistent storage.
Cloud local disks may disappear; this implementation is not a promise of durable
Streamlit Community Cloud storage. Select a shared persistent repository backend
before production integration. P4 has no external scheduler and makes no promise
of notifications. No actual P3/P5 service or UI route has been connected.

## Validation record

Validated on 3 November 2026, Python 3.12 / Streamlit 1.66.1 environment:

- P4 acceptance suite: 76 passed.
- Whole repository: 613 passed (537 existing + 76 P4), in 63.25 seconds.
- Ruff lint and formatting checks: passed for all P4 Python files.
- Offline demonstration: passed, one simulated business invocation.
- Python 3.10 syntax compatibility and staged whitespace checks: recorded in handoff.

The tests use mock actions and real local SQLite databases; they do not measure
live LLM accuracy or prove cloud deployment persistence.

Important coverage: confirm/reject/expire, repeated and concurrent confirmation,
target revisions and the validation/write race, permission revocation, workspace
and creator isolation, atomic audit failures, lost claim acknowledgement,
restart and separate-process persistence, malformed receipts, proposal retry keys,
and regression of the existing application.

SQLite transaction design follows the
[SQLite transaction reference](https://www.sqlite.org/lang_transaction.html) and
[Python 3.10 sqlite3 reference](https://docs.python.org/3.10/library/sqlite3.html).
