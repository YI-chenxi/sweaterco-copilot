-- SweaterCo account and persistent chat schema.
-- Person 3 owns public database migrations.

CREATE TABLE IF NOT EXISTS account_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS account_login_attempts (
    attempt_key TEXT PRIMARY KEY,
    window_started_at TIMESTAMPTZ NOT NULL,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1)
);

CREATE TABLE IF NOT EXISTS account_workspaces (
    workspace_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES account_users(user_id)
        ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS account_workspaces_user_idx
    ON account_workspaces(user_id);

CREATE TABLE IF NOT EXISTS account_sessions (
    session_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES account_users(user_id)
        ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    token_hash CHAR(64) NOT NULL UNIQUE,
    FOREIGN KEY (user_id, workspace_id)
        REFERENCES account_workspaces(user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS account_sessions_owner_idx
    ON account_sessions(user_id, workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS account_sessions_expiry_idx
    ON account_sessions(expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id, workspace_id)
        REFERENCES account_workspaces(user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS chat_threads_owner_idx
    ON chat_threads(user_id, workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id UUID PRIMARY KEY,
    thread_id UUID NOT NULL REFERENCES chat_threads(thread_id)
        ON DELETE CASCADE,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    request_id TEXT,
    reply_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (thread_id, sequence)
);

CREATE INDEX IF NOT EXISTS chat_messages_thread_idx
    ON chat_messages(thread_id, sequence);

CREATE TABLE IF NOT EXISTS chat_agent_states (
    state_id UUID PRIMARY KEY,
    thread_id UUID NOT NULL UNIQUE REFERENCES chat_threads(thread_id)
        ON DELETE CASCADE,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    state_json JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Automatically provision a private workspace for each registered user.
CREATE OR REPLACE FUNCTION account_create_default_workspace()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO account_workspaces (
        workspace_id,
        user_id
    )
    VALUES (
        NEW.user_id,
        NEW.user_id
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER account_user_default_workspace
AFTER INSERT ON account_users
FOR EACH ROW
EXECUTE FUNCTION account_create_default_workspace();

-- Stable request identity for retry-safe conversation persistence.
CREATE TABLE IF NOT EXISTS chat_turn_requests (
    request_id TEXT NOT NULL,
    thread_id UUID NOT NULL REFERENCES chat_threads(thread_id)
        ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('processing', 'completed', 'failed')
    ),
    request_fingerprint CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (thread_id, request_id)
);

CREATE INDEX IF NOT EXISTS chat_turn_requests_status_idx
    ON chat_turn_requests(thread_id, status);


-- Ensure each message request belongs to the same thread.
ALTER TABLE chat_messages
ADD CONSTRAINT chat_messages_request_fk
FOREIGN KEY (thread_id, request_id)
REFERENCES chat_turn_requests(thread_id, request_id)
DEFERRABLE INITIALLY DEFERRED;

-- Each request can have at most one user message and one assistant reply.
CREATE UNIQUE INDEX chat_messages_request_role_unique
ON chat_messages(thread_id, request_id, role)
WHERE request_id IS NOT NULL AND role IN ('user', 'assistant');
