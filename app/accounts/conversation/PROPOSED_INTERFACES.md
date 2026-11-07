# Provisional integration contract: Account Person 4

Status: P4-local proposal. P2/P3 have NOT signed off on these method names,
schema changes, or storage implementation. Adapt the finalized team services
instead of implementing authentication or SQL in P4.

Python 3.10+. Public types and Protocols are in contracts.py.
Persistence envelopes use schema_version=1 and kind=chat_session / agent_reply.

## Ownership and scope

P2 validates authentication and supplies an immutable Principal:
user_id, workspace_id, session_id. session_id identifies the authenticated login
session; it is not the chat thread ID and must not be the raw login token.

IdentityProvider.require_principal(session_handle) must validate expiry,
revocation, logout and membership on every call. The handle is opaque to P4.
Do not pass an editable UI/model user_id as proof of identity. Do not persist or
log the login handle in messages, metadata, request receipts or exception text.
P4 fails closed when this provider is absent.

P3 owns conversation lifecycle, SQL schema/migrations, transactions, request
reservations and concurrency. P4 does not add a users table, auth implementation
or a production repository.

P4 owns safe JSON codecs, detached restoration and per-turn orchestration.
P5 owns UI/session integration. No existing core/UI entry point is changed by
this standalone branch.

## Identity and transaction boundary

P4 revalidates identity at read, pre-claim, pre-execution, pre-commit and
post-commit response boundaries. It checks owner/workspace on snapshots and
receipts as a second defense. P3 must also apply ownership and deletion checks
inside every operation, never only in a previous load.

A Principal is a point-in-time assertion. If logout/revocation must prevent a
concurrent commit atomically, P2/P3 must agree on a transactional active-session
check or equivalent. Revalidation alone does not eliminate that race.
No user-specific state may be placed in a shared cached service/global variable.

## Repository operations

All returned records are detached copies. Unknown or deleted threads are never
silently recreated. Foreign owners/workspaces must be denied on every method.

### load(principal, thread_id) -> ConversationSnapshot

Return a consistent snapshot of messages, latest state and revision.

- New empty thread: revision=0, messages=(), state_document=None.
- Completed turns: exactly two messages per revision, in user/assistant pairs.
- Message IDs and request IDs are canonical UUID strings.
- sequence starts at 1 and is consecutive within the thread.
- Paired messages share request_id; separate turns use separate request IDs.
- User message: original user text, reply_document=None.
- Assistant message: content equals decoded AgentReply.text and includes the
  complete versioned reply_document.
- Every nonempty thread has the latest complete versioned state_document.
- Snapshot includes thread_id, owner_user_id and workspace_id.

P4 validates these invariants before displaying history or invoking the agent.
If your finalized message format differs, translate it in the P3 adapter.
Imported old histories need an explicit migration; P4 does not silently fill
missing state with a blank ChatSession.

### begin_turn(principal, thread_id, request_id, user_text, expected_revision)

Return TurnClaim with one of these statuses:

| Status | Meaning | P4 behavior |
| --- | --- | --- |
| acquired | New request reservation committed | Invoke Agent once |
| completed | Same request/text already committed | Return original receipt; no execution |
| in_progress | Reservation exists, result not resolved | Return REQUEST_IN_PROGRESS |
| uncertain | Execution/save outcome needs reconciliation | Return RECONCILIATION_REQUIRED |

Atomic rules:
1. Verify ownership, membership rules and thread existence.
2. For an already known request ID, check original text. Different text is
   CONFLICT. A completed request returns its original receipt even if the
   caller's new expected_revision reflects later conversation turns.
3. For a new request, compare expected_revision with current revision and
   reject any outstanding/uncertain request on that same thread.
4. Persist reservation before returning acquired. Use unique constraints or
   equivalent serialization across processes; a Python-only lock is insufficient.
5. Release DB transaction before calling the agent. Never hold a DB lock while
   waiting for model/tool calls.

On a lost begin acknowledgement, P4 has not invoked the agent. The reservation
may nevertheless exist; P4 does not take it over automatically.

### commit_turn(..., expected_revision, reply_document, state_document)

Return TurnReceipt. Within ONE transaction:
- Recheck ownership, thread existence and deletion; validate reservation.
- Compare current revision with expected_revision.
- Append the original reserved user text and complete assistant reply.
- Save state_document as latest state.
- Increment revision exactly once.
- Mark request completed and persist immutable replay receipt.

The receipt contains thread/owner/workspace/request identity, original user text,
base_revision, revision, complete reply_document and state_document.
revision = base_revision + 1. An acquired request's base revision must equal the
snapshot used for execution.

Repeated commit of a completed request returns the same receipt. Never overwrite
later state with an old replay. Do not return success before the transaction
commits. On failure there must be no partial user-only or reply-only turn.
A deleted thread must not be resurrected by a delayed agent response.

### mark_uncertain(principal, thread_id, request_id, error_code)

Retain the reservation and mark it for review. Store a safe code, not raw
exception text. Never modify a completed receipt. Never automatically release
a reservation because P4 encountered an exception or its timeout expired.

If this operation fails, the original reservation still blocks execution.
This is why begin_turn must durably reserve BEFORE the agent call.

## Request idempotency and recovery

P5 creates one UUID per send attempt and retains the same UUID/text for network
retries. A subsequent intentional user message gets a new UUID. Reusing an ID
with changed text is an error.

SavedTurn returns reply, committed_revision and replayed, but NOT an old writable
ChatSession. After success/replay, reload current history and state. Replaying
turn 1 after turn 2 must not replace the live thread with turn 1's snapshot.

Do not market this as general exactly-once external effects. A crash between an
external tool effect and result persistence cannot be solved by a chat-state
transaction alone. Effectful tools must use their own confirmed action IDs and
idempotency contracts.

Uncertain/in-progress records need a P2/P3/P4-agreed reconciliation policy.
No automatic recovery endpoint or scheduler is implemented here. Do not advise
users to bypass a blocked reservation with another request ID.

## Codec rules

- Complete envelope with exact fields and explicit version; no pickle/eval.
- Unknown versions raise UNSUPPORTED_SCHEMA; no silent reset/fallback.
- Known task statuses are retained, including completed and failed.
- User maps are tagged as mappings to prevent collision with metadata type tags.
- Metadata permits JSON values, finite floats, tuple, date, datetime and four
  allowlisted project enums. No arbitrary import/class reconstruction.
- Duplicate JSON keys, invalid references, bad types, NaN/Infinity, unknown tags,
  excessive depth and oversized documents fail explicitly.
- Dates and typed metadata survive JSON roundtrips; unsupported future model
  types require an explicit codec/version change.
- Decoding is side-effect free. Loading history never replays model/tool calls.

## Safe error codes and UI handling

| Code | UI action |
| --- | --- |
| INVALID_ARGUMENT | Ask user to correct the request; do not send |
| UNAUTHENTICATED / IDENTITY_CHANGED | Clear displayed user state and require valid login |
| FORBIDDEN / NOT_FOUND | Do not expose foreign history; refresh the user's thread list |
| INVALID_STATE / UNSUPPORTED_SCHEMA / UNSUPPORTED_VALUE / PAYLOAD_TOO_LARGE | Explain data cannot currently be restored/saved; preserve it for diagnosis |
| CONFLICT | Reload current thread; do not auto-reexecute |
| REQUEST_IN_PROGRESS | Display processing/outcome pending; do not execute again |
| RECONCILIATION_REQUIRED | Outcome needs review; do not retry tools |
| STORAGE_ERROR | Storage unavailable before confirmed execution |
| STORAGE_OUTCOME_UNKNOWN | Query using same request ID; never show unacknowledged success |
| AGENT_ERROR | Execution did not finish normally; reservation needs review |

An error after execution does not prove that tools did nothing. P4 does not
return partial output as a successfully persisted chat turn. Raw exceptions
must not be shown to users.

## Integration acceptance: pending real P2/P3 services

1. Valid login: create thread, send a realistic business request, logout/login,
   load history and continue a pending clarification using original state.
2. A second user/workspace cannot read, list, mutate or deduplicate against a
   foreign thread, even with copied UUIDs.
3. Real database/process restart restores the same messages, evidence, task
   statuses, active tasks, global issues and task counter.
4. Two processes/tabs sending concurrently cannot both acquire new requests for
   the same revision; same-ID retry never invokes the model/tool twice.
5. Lose acknowledgement after commit: same-ID retry returns the receipt without
   another execution. Lose acknowledgement after reservation: no takeover.
6. Agent failure or commit failure leaves no partial turn and no automatic retry.
7. Expired/revoked login, thread deletion and concurrency are checked at commit.
8. Invalid version/data is retained and reported, not replaced with empty state.
9. UI switching users/threads never reuses another thread's in-memory state.
10. External confirmed actions have a separate idempotency/reconciliation policy.

Test-only FakeRepository exercises the intended contract under a local RLock.
It does NOT verify SQL transactions, schema constraints, crash recovery, cloud
persistence, browser cookies or actual authentication. These acceptance items
remain integration work, not completed production features.
