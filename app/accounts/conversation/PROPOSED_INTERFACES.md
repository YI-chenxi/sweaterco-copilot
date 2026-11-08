# P4 internal contracts and real P1 P2 P3 adapters

This replaces the initial proposal's assumptions. These are internal P4 types,
not a demand that P3 rename/reimplement its published repository.

## Verified dependency revisions

P1 f5dd3d2, P2 e67aa3c, P3 0d2f90f. See README for full commit IDs.
P4 changes no shared schema, SQL migration, Agent entry point or main UI file.

## Actual mapping

| P4 internal operation | Real implementation |
| --- | --- |
| IdentityProvider.require_principal | P2 resolve_current_user plus P3 workspace membership |
| load | P3 list_messages/get_state inside one guarded short transaction |
| begin_turn | Inspect durable request under row lock; delegate new reservation to P3 begin_turn |
| commit_turn | Delegate P3 complete_turn inside one guarded short transaction |
| mark_uncertain | Scoped update processing -> failed in existing P3 request table |
| account register/authenticate | P1 AuthService |
| login/resolve/logout | P2 SessionService with stable configured pepper |
| create/list/rename/delete thread | P3 repository behind trusted facade and transactional session check |

## Differences from the original proposal

1. P3 commits the user message when beginning a turn, not together with its
   assistant reply. A ConversationSnapshot can contain one PendingTurn and a
   trailing user message. Completed pairs still correspond to revision.
2. Processing user messages are visible as pending, not fake successful replies.
   Missing state is allowed only for a new thread with revision zero and at most
   its declared pending user message. Missing state on a completed thread is invalid.
3. P3 begin_turn returns processing for both a new request and a retry. Calling
   Agent whenever it says processing would duplicate execution. The P4 adapter
   first locks the owned thread, checks for an existing request or any unresolved
   request, compares revision, and only then delegates a new reservation.
4. P3 has processing/completed/failed. P4 maps processing to in_progress and failed
   to uncertain. No unsafe automatic retry/lease takeover is added.
5. P3 retains latest state and completion fingerprint/revision/assistant ID,
   rather than every historical state. TurnReceipt.state_document can be None
   ONLY for a replay. The historical reply is restored, the latest state is loaded
   separately, and SavedTurn does not expose writable old state.
6. Old completed requests without 002 completion identity are rejected for review.
7. P4 does not add request/error columns. Failed processing uses existing failed
   status; the original diagnostic code is not stored in that table.

## Transaction ownership

PostgresConversationRepository owns the outer transaction and connection.
A narrow borrowed connection exposes cursor(), while commit/rollback/close from
P3 are no-ops. P3 reads/writes therefore participate in the P4 transaction.
On any P3 exception the outer owner rolls back, and errors are safely mapped.

Lock order: live account session and workspace, then owned thread. Each
load/reservation/completion is short. There is NO connection/transaction held
while the Agent/model is running.

Thread FOR SHARE keeps message/state reads coherent because P3 writes use
FOR UPDATE on that thread. Reservation FOR UPDATE serializes request checks and
the P3 user-message insert. Completion uses P3's unchanged atomic assistant/state
and immutable-receipt logic. Database ownership and live-session checks are
inside the same transaction; revoked sessions cannot authorize a later write.

A connection/commit failure after a write is STORAGE_OUTCOME_UNKNOWN, never
success. The original reservation is kept; a completed receipt cannot be changed
back to failed. Repeating the same ID reads its existing result; changing the ID
does not bypass unresolved work.

The adapter relies on the inspected P3 transaction/cursor behavior. A change to
P3 committing/closing semantics or query ownership requires re-review. Do not
bypass this facade and independently call P3 begin_turn then invoke the Agent:
that bypasses P4's execution reservation rules.

This is not a general exactly-once guarantee for external business effects.
Effectful tools still require their own confirmed-action idempotency contracts.

## Safe UI API

AccountBackend accepts credentials for register/login, and otherwise accepts a
browser token plus resource IDs. Identity IDs are never editable UI parameters.
P3's default private workspace mapping is used only AFTER successful P1
authentication and checked by P2.

LoginResult hides cookie instructions from repr, but its cookie.value remains a
sensitive one-time bearer credential for the browser adapter. Do not serialize
the login result to logs/history/session_state, and do not put it in a URL.
P4 does not create HttpOnly cookies by injecting browser JavaScript.

All service errors use fixed safe messages. Existing P1 errors are preserved:
INVALID_USERNAME, INVALID_PASSWORD, USERNAME_TAKEN, INVALID_CREDENTIALS,
RATE_LIMITED, STORAGE_ERROR. P2 INVALID_SESSION -> UNAUTHENTICATED;
UNAUTHORIZED -> FORBIDDEN. Unexpected provider errors -> STORAGE_ERROR.
Missing drivers/services/configuration -> CONFIGURATION_ERROR.

UI must distinguish conflict, processing, reconciliation, unknown save outcome
and invalid/unsupported state from successful completion. Reload current history
after save/replay. On logout/account switch clear all user display/caches and
pending request state. These UI changes belong to P5.

## Configuration and migration

DATABASE_URL comes from injected secrets=st.secrets; P3's
create_connection_factory receives it. ACCOUNT_SECRET must be a stable random
server secret of at least 32 UTF-8 bytes. P4 derives independent session and login
throttle keys with labeled HMAC. No ephemeral per-process fallback is used.
Restarting instances with the same stable secret preserves token verification;
changing it invalidates existing sessions.

P3 migrations 001 and 002 are the single schema authority. No migration runs
automatically on application startup. P1 returns canonical UUID strings; P3 TEXT
columns hold those strings and P2 accepts them. Do not change column types or
run P2 schema.sql during P4 integration.

## Validation boundary

Scripted DB-API tests execute actual P3 repository code and verify delegation,
transaction ownership, state/reply mapping and error behavior. P1/P2 integration
tests execute real services with in-memory repositories. Neither proves real
PostgreSQL row locks, network failures, cloud persistence or browser cookies.

Opt-in PostgreSQL tests create a random isolated schema, apply the unchanged P3
migrations only there, and remove that schema afterward. They require a dedicated
connection and explicit opt-in; no production URL is implicitly discovered.
Cloud/application tables are not migrated or cleaned by those tests.
