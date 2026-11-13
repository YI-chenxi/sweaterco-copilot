# Persistent account conversations

This package connects authenticated accounts to private PostgreSQL chat history.
`build_account_backend(agent=..., secrets=...)` creates the UI-facing
`AccountBackend`. The caller supplies a verified session handle for each
conversation operation; user and workspace IDs are resolved server-side.

`AccountBackend` supports login, logout, listing and managing threads, opening
saved conversations, and saving ordinary or Quick Workflow turns. Registration
exists in the backend but the deployed login-only UI disables it. Saved turns
include the reply, evidence, and conversation state. A request ID lets the
storage layer return an existing completion receipt without rerunning the agent.

Apply account storage migrations `001` and `002` before enabling PostgreSQL
mode. Keep the database URL and stable account secret in Streamlit Secrets; see
the repository README for setup. The package does not create browser cookies.

Run the focused tests with:

```powershell
python -m pytest -q tests/accounts/conversation
```
