"""Tests for storage data contracts."""

from datetime import datetime, timezone

from app.accounts.storage.models import (
    AgentStateRecord,
    ChatStorageError,
    ChatThread,
)


def test_storage_error_has_safe_code():
    error = ChatStorageError("CONFLICT")
    assert error.code == "CONFLICT"
    assert str(error) == "CONFLICT"


def test_thread_contract():
    now = datetime.now(timezone.utc)
    thread = ChatThread(
        thread_id="thread-001",
        user_id="user-001",
        workspace_id="workspace-001",
        title="New conversation",
        created_at=now,
        updated_at=now,
    )
    assert thread.user_id == "user-001"


def test_state_revision_contract():
    now = datetime.now(timezone.utc)
    state = AgentStateRecord(
        state_id="state-001",
        thread_id="thread-001",
        schema_version=1,
        state_json={"tasks": []},
        revision=1,
        updated_at=now,
    )
    assert state.revision == 1
