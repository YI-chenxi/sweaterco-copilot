# Account UI

The Streamlit account components provide sign-in, sign-out, account status,
and private conversation navigation. The deployed login-only mode requires
pre-provisioned users and PostgreSQL storage migrations `001` and `002`.

Configure these top-level Streamlit Secrets for login-only mode:

```toml
ACCOUNT_LOGIN_ONLY = true
DATABASE_URL = "postgresql://..."
ACCOUNT_SECRET = "a stable secret of at least 32 UTF-8 bytes"
```

For a shared recording demo, provision `sweaterco_demo_guest` in the same
database and set `DEMO_LOGIN_PASSWORD` to its password. The form prefills this
account but still requires the visitor to click **Sign in**. Users of that
shared account can see its conversation history. Without the demo secret,
sign-in fields start blank.

Saved conversations remain in PostgreSQL across app restarts. A fresh
Streamlit page session may require sign-in again. See the repository README
for the local `demo.cmd` mode and other deployment details.
