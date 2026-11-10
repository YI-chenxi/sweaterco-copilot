CREATE TABLE IF NOT EXISTS account_sessions (
    session_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL,
    token_hash CHAR(64) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS account_sessions_owner_idx
    ON account_sessions (user_id, workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS account_sessions_expiry_idx
    ON account_sessions (expires_at)
    WHERE revoked_at IS NULL;
