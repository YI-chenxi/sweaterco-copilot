# Person 2 deployment acceptance checklist

The following checks are part of Person 2's delivery acceptance, but cannot be
reported as passed by the isolated service/mock suite. Run them against the
actual deployment after the login provider, UI cookie adapter, and PostgreSQL
configuration are available. Record deployment URL/environment, tested build,
browser, timestamp, and result. Do not record credentials or bearer tokens.

## Login and cookie lifecycle

- [ ] Sign in through the deployed login provider; confirm the server receives
  the provider-verified identity and creates a session only after successful
  verification.
- [ ] Inspect the browser cookie metadata: `HttpOnly`, `Secure`, expected
  `SameSite`, `Path`, and finite expiry/max-age are present.
- [ ] Confirm no credential, token, or secret appears in URL parameters, page
  state, application logs, or session database rows in plaintext.
- [ ] Confirm an unknown, expired, revoked, or tampered cookie is rejected and
  cannot produce a current-user context.
- [ ] Log out; verify the server revokes the session and the matching browser
  cookie is cleared. Replay the old cookie and confirm it remains invalid.
- [ ] Restart/redeploy the application while preserving PostgreSQL; confirm an
  unexpired session resolves after restart and a revoked session stays revoked.

## Workspace and resource authorization

- [ ] Attempt to create a session for a workspace not mapped to the verified
  user; the server must reject it.
- [ ] With two test users/workspaces, attempt to read, update, delete, or
  continue another user's thread, message, and conversation state by supplying
  their IDs; every operation must be denied by server-side ownership checks.
- [ ] Confirm workspace switching uses a server-validated membership and does
  not inherit the previous workspace's thread access.
- [ ] Confirm model output and editable browser fields cannot set the current
  user or override resource ownership.

## Evidence to retain

- [ ] Record the production adapter/database version and schema migration ID.
- [ ] Record results for login success/failure, cookie flags, expiry, logout,
  restart, workspace mapping, and cross-user resource denial.
- [ ] Keep mock/unit results and deployed-environment observations as separate
  evidence. A passing mock run is not a live deployment result.
