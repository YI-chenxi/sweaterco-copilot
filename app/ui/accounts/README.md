# Account UI

## Login-only deployment on Streamlit Community Cloud

Keep the existing app on `integration/intent-enhancement` with entrypoint
`app/ui/streamlit_app.py`. After this change is merged, add the following
top-level values to that app's Secrets alongside the existing model settings:

```toml
ACCOUNT_LOGIN_ONLY = true
DATABASE_URL = "existing PostgreSQL connection URL"
ACCOUNT_SECRET = "existing stable account secret, at least 32 UTF-8 bytes"
```

Use the database where the demo accounts were created and migrations 001/002
are already applied. Restart the app and sign in with a pre-provisioned
account. In an incognito window, verify that only sign-in is offered, run a
Quick Workflow, close the window, and sign in again to recover its result
from Recent Chats. A second account should have a separate history.

The P2 login handle lives in the server-side Streamlit session. A fresh page
session can require sign-in again. Saved chats and their Evidence still live
in PostgreSQL. This deployment does not need `ACCOUNT_PUBLIC_URL` or the
Render cookie endpoint. If `ACCOUNT_LOGIN_ONLY` is omitted, the original
anonymous mode remains available. Enabling it without valid database
configuration keeps the workspace closed. This mode reuses a bounded pool
of PostgreSQL connections across requests.

For a shared recording demo, first provision the dedicated account
`sweaterco_demo_guest` in the same database. Add `DEMO_LOGIN_PASSWORD` to the
app's top-level Secrets using that account's password. A fresh sign-in form
will prefill both fields and still require the user to click **Sign in**.
Treat this account's credentials and history as public to visitors. Remove
`DEMO_LOGIN_PASSWORD` to return to blank fields, and retire the guest account
when public access to its history is no longer needed.

## Earlier UI handoff

## Phase 1 authentication

`render_account_panel(backend, session)` in `panels.py` renders registration,
sign-in, sign-out, and account status. The host provides P4's `AccountBackend`
and a server-side `BrowserSession` adapter. The panel reads the existing UI
language and uses native Streamlit widgets that inherit the host theme.
Registration saves an account and leaves sign-in as a separate step.

The panel has no production cookie adapter yet. With no adapter, it shows a
configuration message and disables account actions. Tests provide an in-memory
adapter and a fake backend, with neither Supabase nor browser credentials.
Cloud login remains disabled until an approved secure session transport is
wired into the deployment entry point. The only display identity kept in Streamlit session
state is verified against the backend session before reuse. On a new browser
connection the backend principal has no username, so the panel shows a generic
signed-in status until a trusted profile lookup is available.

P2/P4 and the deployment owner still need to agree how the server applies and
clears `HttpOnly`, `Secure`, `SameSite=Lax` cookie instructions and how Streamlit
gets the new request after sign-in or sign-out. `st.context.cookies` can read
the initial request, but cannot apply a cookie. Verify renewal, expiration,
logout revocation, refresh, and account isolation in the deployed browser.

## Phase 2 personal chat history

`render_recent_chats(backend, session)` in `recent_chats.py` can render in
any Streamlit container, including a sidebar. It uses the same injected
`AccountBackend` and `BrowserSession` as the account panel. A host that
renders the account panel first should show the history panel only when the
account panel returns a verified principal. The history panel also verifies
the session independently before using any saved view state.

The list is fetched from P4 on each render and is not cached. The panel
creates and opens new conversations, opens existing conversations by their
opaque IDs, renames the open conversation, and requires an explicit
confirmation before deleting it. Backend calls receive the session handle
and, where required, a thread ID from the current list. The panel does not
accept a workspace argument or make authorization decisions on behalf of the
backend. On an unknown write outcome, it displays guidance to check the list
before retrying and does not retry the operation itself.

The panel returns the selected P4 `OpenedConversation` as a read-only view
snapshot, or `None` when no conversation is available. Opening a thread
passes through the original messages and Evidence, including missing reply
data. Selecting Open again fetches a new snapshot. The host should use this
return value directly and drop any previous account-specific view when the
panel returns `None`. The panel does not send Agent turns, install the
restored Agent state, or render the main chat transcript.

The open snapshot, rename draft, and delete confirmation are scoped to the
verified user, workspace, and login session. Logout, session expiry, or an
identity change clears that state and invalidates the previous action
widgets. Native widgets use the host's Daily, Eye Comfort, and Night styles
and existing Chinese or English language preference. Offline tests exercise
the panel with a fake session transport and a mock backend. Cloud use still
requires the secure cookie adapter and deployment entry point integration.

## Phase 3B main workspace

The Co-Pilot entry point accepts an explicit `AccountTestMode` with a fake
backend and fake browser session. In this mode the sidebar renders sign-in
and registration until the backend verifies the session. It then renders
personal Recent Chats and uses the persistent chat adapter for keyboard
sends and read-only status checks. The default entry point still opens the
existing local workspace. There is no browser option to enable fake login
and no live account or database configuration is loaded in test mode.

The authenticated workspace renders the database snapshots with stable
message IDs for Evidence. It scopes the composer, selected thread, drafts,
queued requests, Evidence selection, translation cache and workflow preview
to the verified login. Quick Workflows continue to use the deterministic
tools and display a labeled, session-only result and Evidence. A saved chat
does not contain that preview. The persistent composer can accept reviewed
text at the same submission boundary when the voice frontend is integrated.

Cloud login, cookie issuance and clearing, reconnect behavior and unknown
outcomes after a browser reload require the secure transport and P4
reconciliation decisions described above.

## Phase 3C-A server-side initialization

`initialize_real_account_mode(agent=..., secrets=..., session=...)` in
`real_backend.py` calls P4's existing `build_account_backend`. The future
deployment entry point supplies the existing Agent and a server-only
settings mapping, then passes the returned mode to
`main(real_account_mode=mode)`. Create and reuse the backend for the lifetime
of the configured app instance. No user identity should be attached to the
shared backend. The initializer does not look up application settings,
connect to PostgreSQL, or apply migrations. A configuration error keeps
the account workspace closed and displays a localized generic message.

`AccountTestMode` still uses a separate, explicitly labeled path. The
public default still opens the existing local workspace. Real mode without
an injected `BrowserSession` shows that secure sign-in is unavailable and
offers no account actions. The test-only transport exercises the actual P4
facade with in-memory repositories. It has no role in deployment. Real mode
shows Evidence inline and disables CSV export, including preview export,
until a permission-checked download is available.

Remaining deployment work

- **Deployment owner** confirms the Cloud branch and entry point, injects
  the PostgreSQL URL and a stable account secret through server configuration,
  verifies that P3 migrations 001 and 002 are present, and wires the real
  AccountBackend into the Streamlit Cloud entry point. P5 and the
  deployment owner still need to verify account isolation, refresh, and
  re-login with two accounts and real PostgreSQL in a separate test
  environment.
- **P2 and the deployment owner** approve an HTTP transport that sets and
  clears the P2 Cookie through the server, opens a new Streamlit connection
  after login or logout, and aligns browser expiry with server renewal.
  Validate refresh, reopen, revocation and two-account isolation there.
- **P4** defines read-only receipt lookup or the operator procedure for a
  turn whose outcome is unknown after reconnect. Existing `open_thread`
  reads saved messages and pending state. Refresh and reconnect never
  invoke `handle_turn` or regenerate a request to fill a missing receipt.
- **P5 and the deployment owner** keep private CSV export disabled until
  download requests validate the signed-in identity and thread ownership.

## Phase 4B local verification and PR readiness

The local account baseline is `integration/account-p5-dev` at `025134a`.
It contains the P1 authentication, P2 sessions, P3 storage, and P4 state
recovery merges on top of `fec149e`. All P5 account UI changes are in the
`feat/account-ui` worktree. The normal entry point still opens the local
workspace. Real mode requires explicit server-side injection. Private
Evidence is shown inline in real mode and CSV export stays disabled.

### Checks and results

Run these commands from the account worktree in a development environment
with the test tools available. For offline verification, disable dotenv,
omit deployment settings, and block network access. The full suite below
collects the PostgreSQL cases and marks both live test modules as skipped
before their fixtures can connect.

```sh
PYTHON_DOTENV_DISABLED=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python - <<'PY'
import pytest


class OfflineDatabasePolicy:
    def pytest_collection_modifyitems(self, items):
        for item in items:
            module = item.nodeid.split("::", 1)[0]
            if module in {
                "tests/accounts/conversation/test_postgres_live.py",
                "tests/accounts/storage/test_postgres_integration.py",
            }:
                item.add_marker(pytest.mark.skip(reason="Real PostgreSQL disabled"))


raise SystemExit(pytest.main(
    ["-q", "-p", "no:cacheprovider", "-rs"], plugins=[OfflineDatabasePolicy()]
))
PY
python -m pytest -q -p no:cacheprovider tests/accounts/ui
ruff check --no-cache app/ui/accounts app/ui/streamlit_app.py tests/accounts/ui
ruff format --check --no-cache app/ui/accounts app/ui/streamlit_app.py tests/accounts/ui
git -c core.whitespace=cr-at-eol diff --check
```

- Full repository collection under the offline policy had **864 passed,
  13 skipped, 0 failed, and 0 errors**. The skips were 7 P4 live cases and
  6 P3 PostgreSQL cases. No database was contacted.
- P5 account UI tests had **123 passed, 0 skipped, 0 failed, and 0 errors**.
  Coverage includes P4 with test settings and in-memory repositories, login
  and logout, private thread operations, original messages and Evidence,
  session transitions, duplicate sends, and read-only refresh.
- Ruff lint and format checks passed for **13 Python files**. The Git
  whitespace check passed with the existing CRLF line endings of
  `streamlit_app.py` recognized. The untracked files also passed individual
  whitespace checks. The candidate files contained P5 source, tests, and
  documentation only. Credential-shaped values were not found. Test
  settings and handles in the fixtures are synthetic.

### Phase 4A findings resolved in Phase 4B

- **Sidebar account isolation.** Recent Chats is bound to the workspace's
  verified identity for that render. The sidebar revalidates around thread
  reads and clears private views and account-scoped state when identity
  changes or the session can no longer be verified. Regression tests cover
  account switches and invalidation during listing, opening, and sidebar
  rendering.
- **Preservation of rejected messages.** The account composer enforces P4's
  32,000-character limit before calling `handle_turn()`. Oversized messages
  are restored in full to the input box with a Chinese or English notice.
  The regression tests cover 31,999, 32,000, and 32,001 characters, including
  exact text preservation and zero P4 or Agent calls on rejection.

### Remaining shared UI integration

- **Integrate shared UI changes deliberately.** A read-only three-way
  preview against Voice Input Frontend PR #16 commit `01bc464`
  found three conflict blocks in `streamlit_app.py`. Keep the account
  authentication and UUID boundaries, and route reviewed voice text
  through the same submit action when the voice UI is integrated. The
  locally visible proactive backend references do not change `app/ui/`.
  The actual proactive page branch still needs review with its owner.

Target a team-approved remote base that already contains P1–P4. The
current account baseline exists locally. If the team integrates P1–P4 into
`integration/intent-enhancement` first, use the reviewed integration
branch as the base. Otherwise the team needs a published account
integration base. The local tracking ref at `fec149e` does not contain
these dependencies, so a comparison against it also includes their
backend commits. Keep the account review scoped to P5 changes.

The Phase 4B fixes are ready for Draft PR review once a shared remote
base is available. Cloud deployment and the acceptance checks above
remain pending.
