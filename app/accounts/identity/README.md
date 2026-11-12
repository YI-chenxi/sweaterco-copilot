# Person 1: account identity and credential verification

This module registers username/password accounts and verifies credentials. It
does **not** issue browser cookies, session tokens, workspaces, or authorization
decisions. Person 2's `SessionService.create_session(user_id=..., workspace_id=...)`
must only be called after `AuthService.authenticate` succeeds and a server-owned
workspace mapping has been checked. The returned `AuthenticatedUser` has only
`user_id` and canonical `username`.

## Public contract

```python
from app.accounts.identity import AuthService
from app.accounts.identity.postgres import PostgresAttemptRepository, PostgresUserRepository
from app.accounts.identity.throttle import LoginThrottle

users = PostgresUserRepository(connection_factory)
attempts = PostgresAttemptRepository(connection_factory)
throttle = LoginThrottle(attempts, pepper=stable_server_secret_bytes)
auth = AuthService(users, throttle)
registered = auth.register("Alice_1", "valid-pass12")
verified = auth.authenticate("ALICE_1", "valid-pass12")
# Hand verified.user_id to the session composition layer, not the browser.
```

## Integration handoff

**Person 2 — sessions and authorization.** The login composition layer calls
`auth.authenticate(username, password)` first. Only its successful
`AuthenticatedUser.user_id` may be supplied to
`SessionService.create_session(user_id=verified.user_id,
workspace_id=server_mapped_workspace_id)`. Person 2's authorization repository
must confirm that this user owns the server-mapped workspace before a session
is created. Do not accept `user_id` or `workspace_id` supplied by the form,
URL, or model. Person 1 does not set cookies or mint session tokens. Failed
authentication must never call `create_session`.

**Person 3 — database ownership.** Please fold the two tables below into the
single shared migration sequence and provide a psycopg-compatible connection
factory. Preserve the exact unique constraint name used to classify a
concurrent duplicate username. Decide together whether the session table's
`user_id TEXT` becomes a UUID foreign key or uses another validated mapping;
the Person 1 service returns canonical UUID strings. Apply the users table
before the session table and test this with real PostgreSQL. Provide a cleanup
job for expired login-attempt windows. Person 1 must not ship a competing
migration or directly alter Person 2's schema.

**Person 4 — backend composition.** Construct the user and attempt repositories
from Person 3's connection factory, inject a stable secret into `LoginThrottle`,
then compose `AuthService` and Person 2's `SessionService`. Keep registration,
credential verification, workspace mapping, and session creation as distinct
steps. A successful registration does not implicitly log the user in unless
the product flow explicitly performs a subsequent credential verification.
Publish a small common error mapping and verify that storage failures fail
closed before the UI reports success.

**Person 5 — Streamlit UI.** Collect username and password on registration and
login screens, call the backend composition service, and handle only the safe
identity and public error codes. Do not store password, password hash, bearer
token, or credential-validation state in normal `st.session_state` or logs.
The approved browser-cookie adapter must apply Person 2's cookie instructions;
setting a username in `session_state` is not persistent authentication. Test
refresh, restart, logout, expiry, and account switching in the deployed browser.

**Integration order:** agree on the user/workspace IDs and schema; apply Person
3's migrations; connect Person 1 credential verification to Person 2 session
creation and authorization; then connect Person 5's UI and Person 4's persistent
chat flow. The identity module can be tested independently with fakes while
these other branches are unfinished.

`AuthError.code` is one of `INVALID_USERNAME`, `INVALID_PASSWORD`,
`USERNAME_TAKEN`, `INVALID_CREDENTIALS`, `RATE_LIMITED`, or `STORAGE_ERROR`.
The UI should use one generic message for `INVALID_CREDENTIALS`; never display
database exceptions, hashes, or whether a login username exists. Registration
validates a trimmed, lowercase ASCII username of 3–32 letters, digits or
underscores. Passwords remain byte-for-byte unchanged; the minimum is 6
characters and the maximum is 15 characters. Common passwords are
rejected. The password hasher is Argon2id, with 19 MiB memory, 2 iterations,
and one lane; benchmark this on the final deployment before increasing cost.

## Person 3 schema contract

Person 3 owns the migrations. The adapters expect these tables and named
constraint; do not independently apply a Person 1 migration:

```sql
CREATE TABLE account_users (
  user_id UUID PRIMARY KEY,
  username TEXT NOT NULL,
  username_key TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT account_users_username_key_key UNIQUE (username_key)
);
CREATE TABLE account_login_attempts (
  attempt_key CHAR(64) PRIMARY KEY,
  window_started_at TIMESTAMPTZ NOT NULL,
  attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1)
);
```

The `user_id` returned by this module is a UUID string, matching Person 2's
string service boundary. Person 3 must align foreign key types with Person 2's
session table; its current isolated schema uses `user_id TEXT`. The Postgres
adapters accept an injected psycopg-compatible DB-API connection factory; they
do not own connection settings or credentials. User creation commits before
returning success. The named unique constraint resolves concurrent duplicate
registrations. All SQL is parameterized.

## Attempt budget and operations

Each normalized username has a HMAC-derived key. A PostgreSQL atomic upsert
counts attempts across processes: at most 10 attempts in 15 minutes, whether
the user exists or not. A successful login clears that counter. The HMAC
pepper must be a stable server secret of at least 32 bytes and must not be
committed. The in-memory attempt repository is for tests only; it does not
survive restart. Database errors fail closed. Deployments should also rate
limit by client/network at a trusted ingress, as username-only limits do not
cover distributed attempts across many usernames. Old attempt rows should be
periodically purged after their window; Person 3 owns this maintenance job.

Run offline tests with `python -m pytest tests/accounts/identity -q`.
`test_postgres.py` uses a DB-API fake to check transaction and query behavior.
It does not prove real PostgreSQL integration. Once Person 3's migrations and
connection factory are available, verify unique username contention,
rollback, persistence across restart, and attempt counting against the real
database before declaring deployment ready. The 2026-11-08 local run passed
14 identity tests and 551 repository tests; no live database or deployed
browser test was claimed. A PostgreSQL server is present locally, but no
database credentials were available for this module's live verification.

No email or self-service password reset is included. Recovery requires a
separately designed administrator process; never add an unauthenticated reset
path. The current built-in common-password list is intentionally small; a
maintained larger breach/common-password list should be selected before a
public launch.
