# P5 Watches & Alerts — Phases 1–4 and Phase 5B Verification

Phase 4 adds one-pass due evaluation, fixed pre-deadline baselines, transition-based
alerts, atomic occurrence persistence, and an injected local OrderLookupTool source.
The four existing public management signatures, five allowed watch parameters,
Watch/Alert/CheckRecord models and shared contracts are unchanged. The documented
`run_due_watches(now)` method is now implemented. No production P4 adapter,
external scheduler, notification delivery, authentication or UI wiring is included.

Current Phase 5B status: the in-memory append-only JSON comparison defect is
fixed, shared regression coverage is added, and all 17 P5 Python files pass lint
and format checks. P5 tests: 351 passed; full current-branch suite: 888 passed.
Both runs have zero failures, skips and errors. The module is ready for a scoped
P5 PR review, with production P4/P5 integration still explicitly outstanding.
The Phase 5A section below is a historical snapshot superseded by Phase 5B.

Read the actual team baseline in `docs/proactive/INTERFACE_CONTRACT.md` and
`docs/proactive/DEVELOPMENT_PLAN.md`. The internal revision and execution-context
APIs below are P5 implementation details, not additions to shared public schemas.

## Public management interfaces

| Method | Behavior |
| --- | --- |
| `create_watch(workspace_id, parameters)` | ServiceResult; create or replay the original successful receipt |
| `cancel_watch(workspace_id, watch_id)` | ServiceResult; legacy or internally version-checked cancellation |
| `list_watches(workspace_id)` | Detached JSON-compatible list of active watches in insertion order |
| `list_alerts(workspace_id)` | Detached JSON-compatible list of stored alerts in insertion order |
| `run_due_watches(now)` | One-pass checks for explicitly configured trusted workspaces; list of existing six-key ServiceResults |

Mutations use exactly `ok`, `status`, `id`, `data`, `error_code`, `message`.
Validation gives INVALID_ARGUMENT/MISSING_FIELD/UNSUPPORTED_CONDITION; missing
or wrong-workspace targets give NOT_FOUND; identifier, key and revision conflicts
give CONFLICT. Provider failures give EXECUTION_ERROR. Storage failures give a
sanitized STORAGE_ERROR and **do not prove no write occurred**. Invalid list
arguments raise WatchValidationError; list storage failures raise RepositoryError,
never a misleading empty list. Due-check results are described in Phase 4 below.

Public watch parameters are only `order_id`, `condition_type`,
`check_interval_minutes`, `deadline_at` and `expected_status`. Unknown slots are
rejected. UUIDs are normalized. UTC-aware ISO strings are retained in existing
wire data (`+00:00`); storage metadata uses fixed microsecond precision in UTC.
No version, cancellation flag, execution key or receipt metadata is added to the
public Watch dictionary.

## Phase 1 models and deterministic rules

Watch contains identifiers, workspace and the agreed parameters. CheckRecord
contains identifiers, workspace, timestamp, observed_value, shared Evidence and
outcome. Alert is a matched observation with its own globally unique ID. These
are module-local representations; shared alert lifecycle/linkage schemas still
need integration-owner agreement. JSON observations must contain finite values.

`evaluate_condition(watch, observation, *, now, record_id, baseline=None)` is pure:
caller injects ToolResult facts, evidence, UTC time, IDs and optional baseline.
It neither retrieves data nor saves records, generates alerts or sends messages.
Internal outcomes are `not_due`, `matched`, `not_matched`, `unavailable`.

- `status_equals` supports order status IN_PROGRESS or COMPLETE, not production
  stage or DELAYED. It does not require a baseline.
- `no_progress_by_deadline` evaluates at `now >= deadline_at`. It needs coherent
  current/baseline facts for the same order. Equal activity dates support a match;
  later current activity does not. Missing/inconsistent facts are unavailable.
- Phase 4 now implements the approved fixed-baseline, first-check, occurrence
  deduplication and continued-checking rules below. Creation still validates
  parameters rather than rejecting a missing or completed order up front.
- Source dates have day precision. Business-date timezone and same-day activity
  policy require agreement. A delayed check observes current state, not proof of
  exact historical state at the deadline. Static CSV data is not live monitoring.
- Combining status checks and deadlines is deliberately rejected pending agreement.

## Internal repository boundary

Both InMemoryWatchRepository and SQLiteWatchRepository implement WatchRepository:
existing create/get/cancel/list and save_check/save_alert/list_checks methods,
plus the following P5-local additions:

- `get_watch_state(workspace_id, watch_id) -> WatchState | None`: original Watch,
  opaque decimal-string `version`, private `cancelled` flag. Missing and wrong
  workspace both return None. `get_watch` still returns the original Watch,
  including after cancellation, for compatibility.
- `get_receipt(workspace_id, action_id) -> dict | None`: original successful
  ServiceResult, detached from storage. Storage errors must propagate to callers;
  None means no receipt visible, not authorization to automatically retry P4.
- `cancel_watch_if_version(workspace_id, watch_id, expected_version, action_id)`:
  atomic replay-or-compare-and-cancel. Expected revision is a canonical positive
  decimal string. This method is not a new public WatchService endpoint.

The in-memory implementation uses an RLock, internal versions, fingerprints and
receipts with corresponding behavior, but remains process-local. Checks and alerts
are intended to be append-only snapshots: identical record-ID/content retry is a
no-op; changed content or UUID collisions must conflict. Both repositories now
use the existing canonical serialized JSON comparison for stored record retries,
so JSON booleans and numbers are distinguished consistently (Phase 5B fix). After cancellation, historical identical
record retries are allowed but new check/alert appends conflict. They do not
advance the Watch revision. Phase 4 adds internal `get_runtime`, `list_due_ids` and
`process_due` primitives for atomic check-plus-alert batches and occurrence deduplication.
Raw save_check/save_alert are retained for compatibility; they do not represent
runner occurrences. Do not mix manual record injection with production evaluation.

## SQLite schema

Only P5-prefixed tables/indexes are created; P4 action/audit tables are untouched.

| Table | Purpose and constraints |
| --- | --- |
| `p5_object_ids` | Global UUID registry across Watches, Checks and Alerts; ID primary key and workspace association |
| `p5_watches` | Watch JSON, workspace, internal integer version, cancelled flag, created_at/updated_at; retained tombstones |
| `p5_checks` | Append-only CheckRecord JSON, workspace/watch association and UTC timestamp |
| `p5_alerts` | Append-only Alert JSON, workspace/watch association and UTC timestamp |
| `p5_execution_receipts` | `(workspace_id, execution_key)` primary key; operation, Watch ID, SHA-256 fingerprint, original six-key receipt JSON, created_at |
| `p5_watch_runtime` | Workspace/Watch primary key; indexed next_check_at plus JSON anchor, fixed baseline/capture time and alert latch |
| `p5_watch_runs` | Workspace/Watch/scheduled_at primary key; unique Check ID, optional unique Alert ID, original check receipt; composite foreign keys |

Composite foreign keys enforce workspace/Watch association for checks, alerts and
receipts; Watch/Check/Alert identifiers reference the UUID registry. The registry
prevents cross-type/cross-workspace UUID reuse without revealing existing content.
All reads, receipt lookups and updates are workspace-scoped. Lists expose no
other workspace's data; workspace strings and keys are validated and SQL values
are always bound parameters. The only SQL table-name interpolation selects one
of two hard-coded P5 table names.

Caller supplies a database path whose parent already exists. A file-backed DB
survives repository closure and process restart. `:memory:` is SQLite's ephemeral
mode and is not a durable deployment choice. Close repositories explicitly or
use a context manager. No implicit environment/configuration/credential loading.
Schema creation uses CREATE IF NOT EXISTS and does not change SQLite's shared
user_version. Phase 4 additively backfills missing runtime rows in a transaction
from Phase 3 created_at; it never backfills observations/baselines. Existing Alert
history conservatively seeds an initially latched state. Existing runtime rows,
receipts, cancellation versions and P4 tables are retained. Future incompatible
migrations and DB path/deployment ownership still require coordination.

## Transactions and persistent idempotency

Connection settings match the reviewed P4 SQLite approach: foreign_keys=ON,
busy_timeout=10000 ms, journal_mode=WAL, synchronous=FULL; explicit transactions
with isolation_level=None. Writes use BEGIN IMMEDIATE, then COMMIT or ROLLBACK.
A per-connection RLock protects shared threaded use; SQLite serializes independent
connections/processes. Busy/I/O/commit errors are surfaced, not converted to success.
SQLite/WAL storage must be on a supported local filesystem; hosted deployment
backend and backups are not established by these local tests.

Creation/cancellation transaction order:

1. Look up `(workspace, execution_key)` and compare the normalized fingerprint.
2. If it matches, replay the original receipt before examining current state.
   Reusing the key for different parameters, target, revision or operation conflicts.
3. Otherwise validate the target/workspace/revision as appropriate and apply the
   business change. New Watches start at revision `"1"`.
4. Save the original successful receipt in the **same transaction** and commit.
   Any failure before commit rolls back business state, UUID registration,
   version changes and receipt together.

Create fingerprints include normalized Watch parameters/workspace, excluding the
newly generated UUID so a retry can replay its original ID. Cancel fingerprints
include operation, canonical Watch ID and expected revision. Keys are scoped by
workspace and shared across create/cancel to reject cross-operation reuse.
Only successful mutations have execution receipts; validation, missing-target and
version-conflict failures make no change and do not reserve a key.

A new conditional cancellation requires an active target at the expected revision.
Comparison and UPDATE WHERE workspace/id/version/active run inside the same
BEGIN IMMEDIATE transaction. Successful cancellation advances revision 1 to 2;
matching receipt replay never advances it again. Stale revision or a different
new cancellation of an inactive Watch conflicts. Watch parameters are immutable
and check/alert appends do not affect cancellation revision.

**Phase 3 supersedes Phase 2's create-after-cancel CONFLICT behavior:** replaying
the same creation key now returns the original created receipt, even after
cancellation or restart, without reactivating/recreating the Watch. A different
confirmed creation key can create a separate Watch with identical parameters.
Persistent successful receipts have no automatic expiry/deletion.

Legacy cancellation without an injected context retains repeat-success behavior
using a deterministic P5-local key derived from Watch ID. It cannot protect a P4
proposal's expected revision and must not be used by the strict P4 adapter.

## Mocked executor example, without public signature changes

```python
from contextvars import ContextVar
from app.agent.proactive.watches.repository import CancellationContext
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository

# MOCK ONLY: production authorization/confirmation must happen outside P5.
creation_key = ContextVar("mock_create_key", default=None)
cancellation = ContextVar("mock_cancel_context", default=None)
parameters = {
    "order_id": "ORD-002", "condition_type": "status_equals",
    "expected_status": "COMPLETE", "check_interval_minutes": 15,
}
with SQLiteWatchRepository("/existing/local/directory/p5-demo.sqlite3") as repo:
    svc = WatchService(repo, action_id_provider=creation_key.get,
                       cancellation_context_provider=cancellation.get)
    token = creation_key.set("mock-stable-p4-execution-key-create")
    try:
        created = svc.create_watch("demo", parameters)
        assert svc.create_watch("demo", parameters) == created
    finally:
        creation_key.reset(token)
    # A real adapter captures this revision during proposal validation and passes
    # that ORIGINAL value on execution, never replaces it with a fresh revision.
    proposed_revision = repo.get_watch_state("demo", created["id"]).version
    token = cancellation.set(CancellationContext("mock-stable-cancel-key", proposed_revision))
    try:
        cancelled = svc.cancel_watch("demo", created["id"])
        assert svc.cancel_watch("demo", created["id"]) == cancelled
    finally:
        cancellation.reset(token)
    assert repo.get_receipt("demo", "mock-stable-cancel-key") == cancelled
```

The internal optional `cancellation_context_provider` supplies a frozen
CancellationContext(idempotency_key, expected_version). When configured, missing
or invalid context fails closed; it never silently falls back to legacy cancel.
A ContextVar must be set/reset in finally in the actual execution worker. Propagate
context explicitly across threads; do not change a cached service's providers
per request or use shared mutable globals. A key is retry identity, not proof of
confirmation or authorization.

## P4 compatibility and remaining integration dependencies

Phase 5A review reference: read-only `git ls-remote` verified that GitHub branch
`feat/person4-confirmed-actions-audit` still points to
`40b4127382d40de2a4b64ea1daafa44607779cff`. The matching local Git objects were
reviewed without switching branches, fetching changes, merging or editing P4.
Additional isolated fixtures imported exact copies of P4 source in /private/tmp;
no P4 production files or adapter were added to this branch.
P4 execute receives idempotency_key and expected_version. request_key deduplicates
proposal submission, proposal_id identifies confirmation, and execution key
identifies the business side effect; none should be replaced by Watch ID or by a
new UUID on retry. P4 cancellation validate must return the original target
revision, and its adapter must forward the supplied execution key and revision
through the P5 hooks. Full P4/P5 integration has **not** been verified.

- Integration owner must wire authorization, confirmation and request-local context.
  User/workspace identity must come from a trusted server source. P5 enforces
  storage isolation, not authentication. Anonymous writes must not reach P5.
- P4 treats structurally valid failure ServiceResults as outcome_unknown=False.
  P5 STORAGE_ERROR can instead mean a committed write whose acknowledgement or
  receipt read failed. The integration adapter must conservatively route uncertain
  outcomes through P4's ActionError/requires_reconciliation path, or P4/shared
  owners must agree a policy change. No unagreed public field is added here.
- A later reconciler may inspect the durable P5 receipt with the **same workspace
  and execution key** and verify it against the intended operation. Receipt
  absence or lookup failure does not justify unlocking/replacing a P4 claim or
  blindly executing again. P4 claim recovery/admin endpoint ownership is unresolved.
- P4 proposal/audit and P5 business/receipt transactions are separate even if both
  use the same database file. No cross-module atomicity or full audit integration
  is claimed. Avoid nested write transactions on separate connections; agree a
  shared unit of work or reconciliation policy with the owners.
- Map WatchValidationError and read storage failures in the adapter; preserve the
  six-key result and valid statuses. P4 sample DELAYED is not a valid P5 order
  status; use IN_PROGRESS/COMPLETE. Omit unused optional None parameters before
  submitting P4 commands without dropping required missing fields.
- Agree production DB path/backend, migrations, service lifecycle and backup,
  public cancelled-history visibility and final shared alert/check linkage with
  the team. Phase 4's occurrence linkage and approved baseline are P5-internal;
  tenant-specific real order source binding still needs integration.

## Verification

Use the existing environment from the repository root. Tests use temporary SQLite
files and controlled facts, UTC times and mock contexts; no external API calls,
credentials, .env, dependency installs or P3/P4 implementation imports.

```bash
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/proactive/watches/ -q -p no:cacheprovider
.venv/bin/ruff check --no-cache app/agent/proactive/watches/ tests/proactive/watches/
git diff --check
```

Phase 2.1 baseline: 165 passed (101 Phase 1, 51 Phase 2, 13 Phase 2.1).
Phase 3 updates two earlier after-cancel replay expectations to the approved
persistent original-receipt policy and updates one success mock to implement
receipt retrieval. Phase 1 models/rules/tests remain unchanged.

Final Phase 3 P5 run: **225 passed in 0.39s** (165 previous + 60 Phase 3).
Coverage includes both repository implementations, reopen and a separate writer
process, durable original receipts after cancellation, fingerprint mismatch after
restart, two-connection create/cancel races, stale revisions, request-local thread
contexts, workspace/foreign-key isolation, append-only records, rollback after
receipt/record insertion, closed/corrupt/busy storage, UTC validation and committed
writes with lost acknowledgement or failed receipt reads.

Relevant existing contract/A1/A2 regression command:

```bash
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_contracts.py tests/test_order_lookup_tool.py tests/test_order_search_tool.py tests/test_production_retrieval_tool.py tests/test_workshop_retrieval_tool.py tests/test_order_status.py tests/test_order_feasibility.py tests/test_production_normality.py tests/test_production_risks.py tests/test_a1_a2_integration.py -q -p no:cacheprovider
```

Existing regression result: **53 passed in 0.09s**. These tests exercise existing
local business tools separately; no real order retrieval was wired into P5.
Ruff: **All checks passed!** `git diff --check`: exit 0. Because P5 is currently
untracked from earlier phases, all P5 source/test/README contents also receive
`git diff --no-index --check /dev/null <file>` checks. These pass without warnings.
No P3/P4/shared/UI changes, commits, pushes, merges or PRs are made.


## Phase 4 approved execution semantics

`run_due_watches(now: datetime) -> list[dict]` requires an aware UTC timestamp and
an explicitly injected OrderSource plus `run_workspaces=(...)`. No source/scope
means a validation error, never implicit access to every tenant. Per-workspace
query failures and per-Watch persistence failures yield sanitized error results
and do not prevent other workspaces/Watches from being attempted. A cancelled
Watch or a Watch whose next check is later than now is skipped with no source call.

The caller supplies the actual current observation time, not a historical catch-up
time. The single-runner CLI uses current UTC and deliberately has no backdating
option. A delayed check reads current data once and creates no observations for
missed occurrences. The record time is the later of supplied now and the injected
UTC clock after retrieval finishes. A read crossing the deadline cannot backdate
a baseline; tests inject both logical time and clock deterministically. As in
Phase 1, this does not prove
what the order looked like at the historical deadline. Local CSV is a static
snapshot, not real-time or a promise of new data.

### Fixed baseline and exact deadline

- Newly created Watches have internal anchor_at/next_check_at set to repository
  creation time. With an injected source, WatchService attempts an immediate
  check after the creation/receipt transaction commits. Status checks can match
  immediately; deadline checks can initialize their baseline immediately.
- The first valid source observation strictly BEFORE deadline_at becomes the
  fixed baseline, including evidence and its actual supplied capture timestamp.
  It passes the same order/status/evidence/activity-date tests as Phase 1. Only
  fields consumed by those rules are retained; source warnings or incomplete
  required facts cannot become a baseline. Reads return detached snapshots.
- Successful baseline initialization records not_due and next checks at the exact
  deadline. Failed initialization records unavailable and retries on the creation
  grid, capped at the deadline so it cannot skip that boundary.
- At now >= deadline_at, no baseline may be newly captured. A missing valid
  pre-deadline baseline ALWAYS gives unavailable, including for old/migrated
  Watches; no historical baseline is fabricated. A baseline cannot be replaced.
- At the deadline and afterwards, reuse Phase 1 deterministic evaluation and
  evidence requirements. A completed current order with a valid baseline does
  not produce an inactivity alert. Invalid/regressed/uncertain facts remain unknown.

### Next-check grid and alert behavior

Status checks use `anchor_at + k * interval`; k is the smallest positive step
putting the next check strictly after now. For post-deadline checks, the anchor
is deadline_at. Before a baseline is available, initialization retries use the
creation grid, capped at deadline_at. There is no drift based on time spent doing
retrieval and no historical backfill. Extremely large intervals whose timedelta
or future timestamp cannot be represented fail conservatively without committing
an alert/check; input bounds remain those of the existing positive-integer contract.

Alerts are generated ONLY for a matched CheckRecord with valid source evidence.
A persisted latch prevents repeated alerts while the condition stays matched.
For status_equals, a confirmed not_matched resets the latch, so a later confirmed
match can alert again. Unavailable never resets it. For no_progress_by_deadline,
the latch NEVER resets: at most one generated alert per Watch, with the original
fixed baseline and no new cycle or invented re-trigger semantics. A Watch stays
active and keeps checking; failed, completed or matched checks never cancel it.

Missing orders, malformed required data, retrieval exceptions, warnings or missing
evidence produce a stored unavailable record, no alert and a scheduled next check.
Raw exception messages are not saved or returned. A successful check receipt uses
the existing six keys, status=the CheckOutcome string and data=the existing serialized
CheckRecord; ok=True means that the check was recorded, NOT that its condition
matched or that data was available. Alert records are read via list_alerts. No
public lifecycle, schedule, baseline, link or alert_created field was added.

### Atomicity, retries, concurrency and cancellation

For each candidate, process_due acquires the repository lock (memory) or a
BEGIN IMMEDIATE write transaction (SQLite), then re-checks active state and due
runtime before calling the injected local source. Checks, optional Alerts, the
fixed baseline/latch, original occurrence receipt and next-check information commit
together. No partial alert, baseline or advanced schedule survives rollback.
Watch cancellation versions do not change during checks. A cancellation committed
first skips the source call; a check that acquires the write transaction first
commits before cancellation, retaining its history. Cancellation prevents later checks.

The occurrence key is `(workspace_id, watch_id, scheduled_at)` where scheduled_at
is the persisted next_check_at before processing, NOT the supplied late observation
time. A unique constraint plus atomic advancement to next_check_at > now prevents
duplicates across retries, independent connections and restarts. p5_watch_runs
also links the Check/Alert IDs and retains the original six-key check receipt.
After a successful commit followed by lost acknowledgement, the call can report
STORAGE_ERROR, but repeating the same observation time skips already processed
work. Inspect internal runtime/run receipts rather than assuming no write occurred.
After an uncommitted process crash, SQLite rolls back the batch and leaves it due.
A retry can repeat the side-effect-free CSV read, but cannot duplicate committed
records or alerts. There is no claim of exactly-once external I/O.

Retrieval is deliberately inside this local transaction to avoid concurrent duplicate
reads of the same due occurrence. A slow source holds SQLite's database-wide writer
lock; this design is for the current local CSV source. A future remote/slow source
needs a reviewed claim/lease/fencing design before use. No external retrieval API,
lease worker, background thread or scheduler is implemented.

Immediate bootstrap is a separate check after the creation receipt commits. If it
cannot persist, creation still returns its original successful receipt, a sanitized
warning is logged, and initial due state is retained for the next runner. Retrying
creation replays its receipt without repeating bootstrap. An unavailable source is
a successfully recorded unknown check and follows the configured retry schedule.

### Injection and one-shot usage

```python
from datetime import datetime, timezone
from app.agent.proactive.watches.order_source import OrderLookupSource
from app.agent.proactive.watches.service import WatchService
from app.agent.proactive.watches.sqlite_repository import SQLiteWatchRepository
from app.tools.order_lookup import OrderLookupTool

# Trusted internal caller; bind exactly this workspace to its approved CSV/tool.
source = OrderLookupSource("demo", OrderLookupTool())
with SQLiteWatchRepository("/existing/local/directory/p5-demo.sqlite3") as repo:
    svc = WatchService(repo, order_source=source, run_workspaces=("demo",))
    results = svc.run_due_watches(datetime.now(timezone.utc))
```

OrderLookupSource.fetch(workspace_id, order_id) delegates to the unmodified
OrderLookupTool.execute(order_id=...). It rejects other workspaces before reading.
Tests inject Mock sources; a multi-tenant deployment must inject an authorized
workspace-to-dataset mapping and never fall back to another tenant's dataset.
The four management methods remain independently usable without an order source.

From the repository root, run once and exit:

```bash
.venv/bin/python -B -m app.agent.proactive.watches.runner --database /existing/local/directory/p5-demo.sqlite3 --workspace demo --orders /approved/dataset/orders.csv
```

The CLI emits a JSON list of ServiceResults and labels the CSV snapshot on stderr.
Exit 0 means no errors/unavailable outcomes (including no due Watches); exit 1 means
an unavailable check, a storage/configuration error or another failed result.
No .env/settings/API keys are loaded; no external APIs, external scheduling,
notification delivery or UI are invoked. The CLI is a trusted internal entry point,
not an authenticated public endpoint. P4/P3 orchestration and server authorization
remain integration-owner work; previous uncertain-outcome and revision-propagation
requirements still apply.

### Phase 4 verification

Final P5 suite: **327 passed in 0.95s** (225 prior tests + 102 Phase 4 cases).
All earlier tests remain unchanged. Phase 4 covers both repository implementations,
fixed baselines/restart, exact deadline and crossing-during-read boundaries,
missed checks/grid scheduling, matched/non-matched/unavailable transitions, missing
orders and incomplete evidence, cancellation before/during concurrency, explicit
workspace/source scoping, shared-thread and independent-connection deduplication,
atomic failures at check/alert/run/runtime writes, lost acknowledgement, abrupt
writer-process exit, additive Phase 3 backfill, and real local CSV adapter/one-shot
runner subprocesses. These are simulated/local tests, not live monitoring claims.

Relevant existing contract/A1/A2 regression tests: **53 passed in 0.09s**, using
the same ten-file regression command documented above. Ruff: **All checks passed!**
`git diff --check`: exit 0. All untracked P5 source/test/README contents also pass
`git diff --no-index --check /dev/null <file>` without whitespace warnings.
No P3/P4/shared/AgentService/A1/A2/UI files were changed, and no credentials,
.env files, external APIs or dependency installs were used. No commits, pushes,
merges, PRs, external scheduler, notification delivery or frontend integration.


## Phase 5A final verification and documentation (historical; superseded by Phase 5B)

This phase changes README only. It neither repairs implementation defects nor
formats source/tests. Branch remains feat/watch-alerts at integration baseline
fec149ef98896a9d834eca5906ba8eb71045a3c7 with the P5 work untracked and uncommitted.
No branch switch, merge, rebase, commit, push or PR was performed.

### Actual results

| Verification | Result |
| --- | --- |
| Full current-branch repository pytest suite | **864 passed, 0 failed, 0 skipped, 0 errors in 18.28s** |
| P5 cases included in that full run | **327 passed**: Phase 1 101; Phase 2 51; Phase 2.1 13; Phase 3 60; Phase 4 102 |
| Existing non-P5 tests included | **537 passed** |
| Temporary real-P4/P5 compatibility probes | **4 passed, 0 failed, 0 skipped, 0 errors in 0.09s** |
| P5 Ruff lint | **All checks passed**, exit 0 |
| P5 Ruff format check, Python only | **Failed**, exit 1: 14 files would be reformatted, 2 already formatted (16 Python files) |
| git diff --check and untracked-content checks | Pass; untracked P5 contents checked separately because git diff omits them |
| Candidate-file scope and common secret patterns | 17 P5-owned source/test/README files only; no credential/config/DB artifacts or common token/private-key patterns found |

The full-suite command used the existing virtualenv and a clean child environment.
An ephemeral sitecustomize audit guard blocked credential/config file reads
(including .env/secrets.toml) and network connection/DNS attempts. Tests may create
their own dummy settings values; no real API key was inherited or inspected.
Bytecode/cache writing was disabled, and SQLite files/reports were in /private/tmp.
Third-party pytest auto-discovery was disabled; explicitly declared repository
plugins and fixtures still ran. No repository tests were excluded or skipped.
Only the remote Git SHA check needed network access; no external service/API was
called by tests or application code.

Exact full-suite invocation from the repository root:

```bash
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/private/tmp/p5phase5a/guard PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/private/tmp/p5phase5a/pytest --junitxml=/private/tmp/p5phase5a/full-suite.xml
```

The audit guard and compatibility fixtures are temporary verification artifacts,
not production changes or portable checked-in CI dependencies. Local full-run log
is /private/tmp/p5phase5a/full-suite.log; authoritative counts are also in
/private/tmp/p5phase5a/full-suite.xml. A normal CI command is python -m pytest -q,
with CI-owned credential/network isolation; do not assume it is safe to inherit a
user's real configuration. P4's own 76 tests are not present on this P5 branch and
are not included in 864; no claim is made that all P4 tests ran here.

Additional checks (Python globs deliberately exclude README code blocks):

```bash
.venv/bin/ruff check --no-cache app/agent/proactive/watches/ tests/proactive/watches/
.venv/bin/ruff format --check --no-cache app/agent/proactive/watches/*.py tests/proactive/watches/*.py
git diff --check
```

### Correctness review coverage

All 11 P5 implementation Python files and all five P5 test files were inspected,
including public signatures, data validation, serialization, repository methods,
transaction scopes, due processing, injected source and one-shot entry point.
The README examples/schema/limitations and actual shared interface contract were
reviewed together. Existing tests cover the following boundaries; passing tests
are evidence for those cases, not proof against all possible defects.

| Area | Evidence and review result |
| --- | --- |
| Create/cancel/list and workspace isolation | Phase 2/3 validation, detached values, wrong-workspace NOT_FOUND, source binding and explicit runner scope; no new public fields |
| Pure rules, baseline and UTC deadline boundaries | Phase 1/4 facts/evidence validation, fixed baseline, before/equal/after and read-crossing deadline cases; no historical fabrication |
| UTC scheduling and delayed checks | Fixed creation/deadline grids, one current observation, next time strictly after observed time |
| SQLite restart and transactions | Reopen, independent writer process, abrupt process exit, business/receipt rollback and atomic check/alert/runtime/run writes |
| Version cancellation and durable idempotency | Workspace/key/fingerprint matching, version compare/write in one transaction, retries after cancellation/restart; checks retain cancellation revision |
| Alerts, unknown data and failure isolation | Status rearming only on confirmed non-match, unavailable preserves latch, deadline never rearms; independent Watch continues after retrieval/storage failure |
| Concurrent due execution | Shared threads and independent SQLite connections serialize one committed occurrence/source read, with active/due re-check before retrieval |
| Append-only record retry comparison | **Defect found in memory repository**, detailed below; SQLite behaves correctly |

### Phase 5A defect finding — subsequently fixed in Phase 5B

InMemoryWatchRepository._save_record compares dataclasses with `existing != record`.
Python considers True == 1 and 1 == 1.0, including inside nested dictionaries.
Consequently a CheckRecord or Alert retry using the same ID but changed JSON
observed_value can be accepted as an identical retry. SQLite compares canonical
serialized JSON and correctly raises RepositoryConflict. The stored original is
not overwritten, but changed payloads are incorrectly accepted by the memory
repository, violating the documented append-only conflict behavior and repository
parity. Current tests change status strings and do not cover this type boundary.

An isolated reproduction under /private/tmp/p5phase5a verified eight combinations:
CheckRecord/Alert x bool-to-int/int-to-float x memory/SQLite. All four memory cases
incorrectly accepted changed JSON; all four SQLite cases correctly conflicted.
This reproduction is additional diagnostic evidence, not a failing test hidden
from the reported 864-test run.

Proposed narrow repair after approval: compare
`dumps(record_data(existing))` and `dumps(record_data(record))` in the in-memory
append-only path, reusing the existing serializer rather than Python value
equality. Add shared-repository regressions for bool vs number, integer vs float,
nested values, unchanged retries and retained original content. No shared schema,
public signature or SQLite business behavior needs to change. Then run P5 and
full-repository tests again. This phase did not apply that repair.

Separately, all P5 Python files except __init__.py and order_source.py need Ruff
formatting. A later approved formatting-only step should run the formatter on the
16 explicit Python paths, then repeat lint, format check and tests. No formatter
was applied during Phase 5A. README formatting examples are documentation only
and are not included in the 16-file Python check.

### P4 compatibility probes and integration responsibilities

Exact current P4 models, service, repository and handoff were reviewed. A temporary
adapter fixture (not a production adapter) exercised actual P4 + P5 SQLite code:

1. Create/confirm/repeated confirm, then a due check with the unmodified
   OrderLookupTool and checked-in orders.csv, then version-checked cancellation
   and repeated confirmation. Shared-file table coexistence, audit history,
   persistent business receipt and restart were verified. The fixture uses the
   order's actual CSV status rather than assuming an order status.
2. A target changed after proposal is rejected before another P4 execution.
3. A committed P5 create with lost acknowledgement, conservatively mapped to
   P4 ActionError, sets requires_reconciliation=True and does not execute again.
4. The same uncertain STORAGE_ERROR passed through as an ordinary failure receipt
   is demonstrably marked outcome_unknown=False by P4. This passing negative
   probe confirms the remaining adapter policy requirement; it is NOT safe wiring.

The temporary fixture initially assumed an incorrect ORD-057 CSV status; correcting
that fixture to read the actual source status produced the final four-pass result.
No P5/P4 implementation changes were needed for those compatibility probes.

P4/integration owner still must provide a reviewed production adapter with read-only
validate and execute, preserve the original proposal revision and execution key
via request-local contexts, reject watch.update, enforce trusted actor/workspace
permissions and map uncertain P5 storage outcomes into reconciliation. A business
receipt must be verified against the intended operation before reconciling P4.
The existing P4 claim/audit transaction and P5 business transaction are separate;
sharing a file is not a cross-module transaction. Administrative reconciliation,
production DB path/backend/migrations/backups and UI/Agent routing remain owner
responsibilities. Source binding must be explicit per tenant. External scheduling,
notifications and frontend are deliberately outside the completed P5 phases.

Local CSV/write-lock behavior, day-precision source dates and representable interval
limits remain the documented limitations above. Do not present the module as live
monitoring, a cloud-durable deployment or completed production authorization.

### Change scope and readiness

Git candidate changes remain exactly 17 untracked files under
app/agent/proactive/watches/ and tests/proactive/watches/: 16 Python files plus this
README. No .env, API credentials, private key or generated DB/WAL/SHM file is among
them. Only those candidate source/text files were scanned for common secret
patterns; actual credential files were never opened. Existing tracked/shared files
are unchanged. SHA-256 comparisons confirm this verification phase changed README
only, with no implementation/test edits. git diff --check alone is insufficient
for untracked work, so each candidate also receives a no-index whitespace check.

The planned core P5 backend capabilities are implemented and the existing full
suite passes, but final acceptance is conditional: repair the confirmed in-memory
record-comparison defect, add its regression coverage, apply P5-only Python
formatting and re-verify. Therefore the branch is **not yet recommended as ready
for a completion PR**. Production integration can remain a separately scoped
owner task, clearly documented; these local defects/check failures should be
resolved before presenting P5 as fully verified. This was the Phase 5A assessment;
see Phase 5B below for the approved fix and current readiness.


## Phase 5B targeted repair and formatting — 2026-11-05

This phase fixes only the confirmed record-retry comparison defect, adds its
regressions, formats P5 Python files and updates this README. No new business
functionality, public fields, service signatures or error mappings were added.

### Fix and regression coverage

InMemoryWatchRepository._save_record now compares
`dumps(record_data(existing)) != dumps(record_data(record))`, using the same
existing serialization as SQLiteWatchRepository._insert_record. This replaces
Python dataclass/dictionary equality, which conflated True with 1 and False with
0. Different canonical JSON under the same CheckRecord/Alert ID now raises the
existing RepositoryConflict. Stored original records are preserved. Identical
records, including dictionaries with different key order, remain valid retries.

Record retries use their existing record ID; confirmed Watch creation uses its
existing workspace/execution key and parameter fingerprint. No new identity field
was introduced, and the already-correct creation fingerprint mechanism is unchanged.

New tests/proactive/watches/test_phase5b.py adds **24 parameterized cases**, shared
by the in-memory and temporary-file SQLite implementations:

- CheckRecord and Alert retries with True vs 1, False vs 0, integer vs float, and
  nested boolean/number changes must conflict and retain the original JSON.
- Identical record retries with reordered dictionary keys remain no-ops.
- The same confirmed execution key and equivalent normalized Watch parameters
  return the original receipt without creating another Watch.
- The same execution key with changed parameters returns the existing CONFLICT
  response and retains the original Watch and receipt.

Before the repair, this new file reproduced the defect as **8 failed, 16 passed
in 0.12s**, all eight failures in memory CheckRecord/Alert comparisons. Those are
historical reproduction results, not failures in final verification. Final P5
and full-repository runs below both include the now-passing 24 new cases.

### Exact change scope

Functional implementation change:
- app/agent/proactive/watches/memory_repository.py: import existing JSON helpers
  and use canonical record comparison, plus formatting.

New regression file:
- tests/proactive/watches/test_phase5b.py.

Formatting-only changes to the other 13 existing Python files:
- app/agent/proactive/watches/evaluation.py
- app/agent/proactive/watches/models.py
- app/agent/proactive/watches/repository.py
- app/agent/proactive/watches/rules.py
- app/agent/proactive/watches/runner.py
- app/agent/proactive/watches/serialization.py
- app/agent/proactive/watches/service.py
- app/agent/proactive/watches/sqlite_repository.py
- tests/proactive/watches/test_phase1.py
- tests/proactive/watches/test_phase2.py
- tests/proactive/watches/test_phase2_1.py
- tests/proactive/watches/test_phase3.py
- tests/proactive/watches/test_phase4.py

README is updated. __init__.py and order_source.py were already formatted and
remain unchanged. The formatter changed 14 existing files; the new regression
file already met its formatting rules. A long SQL literal was split into adjacent
literals after indentation formatting; its evaluated query text is unchanged.

AST comparison against the Phase 5B start snapshot verifies that the memory file
contains exactly the intended imports/comparison change and that the other 15
pre-existing Python files retain identical ASTs. This confirms formatting did not
change existing business logic or tests. No unrelated owner's Python was formatted.

### Final verification results

| Check | Actual final result |
| --- | --- |
| All P5 pytest | **351 passed, 0 failed, 0 skipped, 0 errors in 1.10s** |
| Full current-branch repository pytest | **888 passed, 0 failed, 0 skipped, 0 errors in 17.87s** |
| P5 subset in full run | **351** (327 previous + 24 new); non-P5 existing tests **537** |
| Ruff lint | **All checks passed**, exit 0 |
| Ruff format --check, Python only | **17 files already formatted**, exit 0 |
| git diff --check | Pass, exit 0 |
| All untracked P5 text contents | Separate no-index whitespace checks pass |

Final commands from the repository root, using the existing virtualenv:

```bash
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/private/tmp/p5phase5b/guard PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/proactive/watches/ -q -p no:cacheprovider --basetemp=/private/tmp/p5phase5b/p5-verified --junitxml=/private/tmp/p5phase5b/p5-verified.xml
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/private/tmp/p5phase5b/guard PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/private/tmp/p5phase5b/full-verified --junitxml=/private/tmp/p5phase5b/full-verified.xml
.venv/bin/ruff check --no-cache app/agent/proactive/watches/*.py tests/proactive/watches/*.py
.venv/bin/ruff format --check --no-cache app/agent/proactive/watches/*.py tests/proactive/watches/*.py
git diff --check
```

Both JUnit XML reports confirm the exact pass/fail/skip/error counts. The ephemeral
credential/network audit guard from Phase 5A was copied to the Phase 5B temporary
directory. Tests run in a clean environment without inherited API keys, and the
guard blocks .env/credential reads and network connections/DNS. No dependency
installation or external API call occurred. No test was excluded or skipped.
Logs are /private/tmp/p5phase5b/p5-verified.log and full-verified.log. The guard,
logs, XML and temporary databases are local verification artifacts, not files
included in the proposed change or portable CI dependencies.

The branch remains feat/watch-alerts at fec149ef98896a9d834eca5906ba8eb71045a3c7.
Final candidate scope is **18 untracked P5-owned files**: 11 implementation Python,
six test Python and this README. There are no .env, credentials, private keys or
generated SQLite/DB/WAL/SHM files among them; common token/private-key pattern
checks on candidate text found no matches. Tracked shared/team files are unchanged.
No branches were switched and no commit, push, merge, rebase or PR was made.

### PR readiness and unchanged integration limits

The Phase 5A defect and Python formatting blockers are resolved. The branch is
**ready for a scoped P5 backend PR review** based on the tests and checks above;
this does not mean a PR has been created, the code has been staged/committed, or
production integration is complete. The Phase 5A P4 probes remain historical
evidence against commit 40b4127382d40de2a4b64ea1daafa44607779cff; Phase 5B did not
contact the remote, modify P4 or repeat/extend production integration.

P4/integration owner still must provide the reviewed production validate/execute
adapter, propagate stable execution keys and original target revisions, enforce
trusted actor/workspace permissions and route uncertain STORAGE_ERROR outcomes
to reconciliation rather than treating them as definite no-write failures. P4
audit/claim and P5 business transactions remain separate. Repository deployment,
migrations/backups, tenant-specific order-source binding and Agent/UI routing
remain integration responsibilities. Local CSV is a static snapshot; SQLite
holds a writer lock during retrieval, source dates have day precision, and the
existing representable-interval limitations remain. No external scheduler,
notifications, frontend or new monitoring condition was implemented. Stop after
Phase 5B and wait for approval.
