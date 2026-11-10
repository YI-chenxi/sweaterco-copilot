# SessionService (Person 2)

This module independently owns authenticated browser session lifecycle
metadata. It does not authenticate credentials, persist chat messages, or
mutate Streamlit state. Its public contract accepts an already-authenticated
`user_id` and a required `workspace_id`, and returns those trusted values only
after bearer-token resolution. UI code can apply the returned cookie
instructions. Authentication, chat storage, or UI implementations are not
required to run this service's tests.

## API

```python
from app.accounts.session import (
    InMemoryAuthorizationRepository,
    InMemorySessionRepository,
    SessionService,
)

authorization = InMemoryAuthorizationRepository()
authorization.grant_workspace("user-1", "workspace-1")
sessions = SessionService(InMemorySessionRepository(), authorization)
created = sessions.create_session(user_id=auth_user.id, workspace_id=workspace_id)
# UI sets created.cookie using its cookie component. Never put created.token in a URL.
session = sessions.resolve_session(cookie_value)
# Use only session.user_id / session.workspace_id after resolution.
context = sessions.resolve_current_user(cookie_value)
sessions.authorize_thread(cookie_value, request_thread_id)
sessions.authorize_message(cookie_value, request_message_id)
sessions.authorize_state(cookie_value, request_state_id)
sessions.revoke_session(cookie_value)  # logout; repeated logout is harmless
delete_cookie = sessions.cookie_deletion()  # UI clears the same cookie name/attributes
visible = sessions.list_sessions(user_id="user-1", workspace_id="workspace-1")
sessions.revoke_session_by_id(
    session_id=visible[0].session_id,
    user_id="user-1",
    workspace_id="workspace-1",
)
```

`create_session` first checks workspace membership through the injected
server-side `AuthorizationRepository`; it does not trust workspace selection
from a browser without that check. It returns `CreatedSession(session, token, cookie)`. The opaque
random bearer token is returned once and is never included in `Session`. Only a
peppered HMAC-SHA256 digest is passed to the repository. `resolve_session` maps
that bearer token back to server-owned identity and applies expiry/revocation
checks; client-provided session IDs, user IDs, and workspace IDs are not used for
authentication. Session-list and ID-revocation operations require both trusted
`user_id` and `workspace_id`, and the repository enforces that scope atomically.
`revoke_user_sessions(user_id)` supports security events such as
logout-everywhere or credential reset.

All stored timestamps are UTC-aware. The default lifetime is 14 days with
sliding refresh on successful resolution. Cookie instructions default to
`HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`. The service does not set a browser
cookie itself: the UI adapter must apply these attributes through the approved
cookie integration and clear the cookie with matching name/path/security
attributes on logout. The caller owns authenticated identity; this service
checks workspace membership using its authorization repository and does not
implement a login provider or callback flow. `cookie_secure=False` and peppers
shorter than 32 bytes are rejected.

`resolve_current_user(token)` creates an immutable trusted context from a
verified token. `authorize_thread`, `authorize_message`, and `authorize_state`
check server-side ownership; messages and state are resolved through their
parent thread. A front-end resource ID, URL value, model output, or editable
client field is never proof of ownership. `InMemoryAuthorizationRepository`
provides deterministic membership and ownership fixtures for isolated tests.

## Persistence and security boundary

`SessionRepository` is the injected persistence protocol. The included
`InMemorySessionRepository` is a deterministic RepositoryMock for tests and local
development only; it does not survive process restart. The mock uses a
reentrant lock so create, refresh, and revoke operations are atomic in tests.
`PostgresSessionRepository` implements the same contract over an injected
PostgreSQL DB-API connection factory without importing a driver or embedding
credentials. It persists `session_id`, `user_id`, `workspace_id`, `created_at`,
`expires_at`, `last_seen_at`, `revoked_at`, and `token_hash`; enforces uniqueness
on session ID and token hash; uses conditional updates for refresh/revoke; and
scopes administrative/user-list operations to authorized identity. It never stores the
plaintext bearer token. The initial table and indexes are in `schema.sql`;
deployment must apply this schema and configure database TLS, access controls,
and secret management. SQL is parameterized and owner-scoped.

The token pepper is generated per service instance if not supplied. For durable
sessions across restarts, configure the same high-entropy pepper from a server
secret across application instances and persist repository rows. Rotating the
pepper invalidates existing sessions unless a versioned rotation scheme is
added. Use a stable `token_pepper` secret in deployment; never commit it.

## Tests

Run from repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests\accounts\session -q
```

Tests use a fixed UTC clock, deterministic test-only token factory, in-memory
repositories, and a DB-API fake. They cover cookie flags, token non-persistence,
resolution and sliding expiry, client ID rejection, exact expiration,
logout/revocation, user/workspace-scoped session management and resource access,
workspace membership checks, PostgreSQL parameterization/rollback, naive clocks,
and storage failure mapping. The fake checks adapter behavior but does not prove
integration with a live PostgreSQL server, deployed login-provider behavior,
browser cookie integration, or deployment authorization policies.

## Deployment acceptance

See [DEPLOYMENT_ACCEPTANCE.md](DEPLOYMENT_ACCEPTANCE.md) for the real-environment
login, cookie, expiry, logout, restart, and ownership checks that still require
the deployed login provider, UI cookie integration, and database configuration.
