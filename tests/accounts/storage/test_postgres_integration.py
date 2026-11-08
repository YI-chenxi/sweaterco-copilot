"""Real PostgreSQL integration tests for persistent conversations."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.accounts.storage.models import ChatStorageError
from app.accounts.storage.postgres import PostgresChatRepository


@pytest.fixture
def chat_context(pg_connection_factory):
    user_id = str(uuid4())
    username = "p3pytest_" + uuid4().hex[:12]

    with pg_connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO account_users
                (user_id, username, username_key, password_hash, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    username,
                    username,
                    "test-only-placeholder",
                    datetime.now(timezone.utc),
                ),
            )

    repo = PostgresChatRepository(pg_connection_factory)
    thread = repo.create_thread(user_id, user_id, "Integration test")

    yield repo, user_id, thread.thread_id

    with pg_connection_factory() as connection:
        with connection.cursor() as cursor:
            # Delete only this test user's threads first.
            # Messages, requests and Agent states cascade automatically.
            cursor.execute(
                """
                DELETE FROM chat_threads
                WHERE user_id = %s AND workspace_id = %s
                """,
                (user_id, user_id),
            )

            cursor.execute(
                "DELETE FROM account_users WHERE user_id = %s",
                (user_id,),
            )


def test_conversation_survives_reconnect(chat_context, pg_connection_factory):
    repo, user_id, thread_id = chat_context
    request_id = str(uuid4())

    repo.begin_turn(user_id, user_id, thread_id, request_id, "Hello")

    repo.complete_turn(
        user_id, user_id, thread_id, request_id,
        "Hi!",
        {"text": "Hi!"},
        {"step": 1},
        schema_version=1,
        expected_revision=0,
    )

    reopened = PostgresChatRepository(pg_connection_factory)

    messages = reopened.list_messages(user_id, user_id, thread_id)
    state = reopened.get_state(user_id, user_id, thread_id)

    assert [message.role for message in messages] == ["user", "assistant"]
    assert [message.sequence for message in messages] == [1, 2]
    assert state is not None
    assert state.revision == 1
    assert state.state_json == {"step": 1}


def test_old_request_replay_after_newer_turn(chat_context):
    repo, user_id, thread_id = chat_context
    requests = [str(uuid4()), str(uuid4())]

    for index, request_id in enumerate(requests):
        repo.begin_turn(
            user_id, user_id, thread_id, request_id, f"Question {index}"
        )
        result = repo.complete_turn(
            user_id, user_id, thread_id, request_id,
            f"Answer {index}",
            {"text": f"Answer {index}"},
            {"step": index},
            schema_version=1,
            expected_revision=index,
        )
        assert result["revision"] == index + 1

    replay = repo.complete_turn(
        user_id, user_id, thread_id, requests[0],
        "Answer 0",
        {"text": "Answer 0"},
        {"step": 0},
        schema_version=1,
        expected_revision=0,
    )

    assert replay["already_completed"] is True
    assert replay["revision"] == 1

    with pytest.raises(ChatStorageError) as exc:
        repo.complete_turn(
            user_id, user_id, thread_id, requests[0],
            "Changed answer",
            {"text": "Changed answer"},
            {"step": 999},
            schema_version=1,
            expected_revision=0,
        )

    assert exc.value.code == "CONFLICT"
    assert len(repo.list_messages(user_id, user_id, thread_id)) == 4
    assert repo.get_state(user_id, user_id, thread_id).revision == 2


def test_failed_state_serialization_rolls_back(chat_context):
    repo, user_id, thread_id = chat_context
    request_id = str(uuid4())

    repo.begin_turn(user_id, user_id, thread_id, request_id, "Question")

    with pytest.raises(ChatStorageError) as exc:
        repo.complete_turn(
            user_id, user_id, thread_id, request_id,
            "Answer",
            {"text": "Answer"},
            {"invalid": object()},
            schema_version=1,
            expected_revision=0,
        )

    assert exc.value.code in {"INVALID_ARGUMENT", "STORAGE_ERROR"}
    messages = repo.list_messages(user_id, user_id, thread_id)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert repo.get_state(user_id, user_id, thread_id) is None


def test_cross_user_cannot_access_private_thread(chat_context):
    repo, user_id, thread_id = chat_context
    other_user = str(uuid4())

    assert repo.list_messages(other_user, other_user, thread_id) == []
    assert repo.get_state(other_user, other_user, thread_id) is None

    with pytest.raises(ChatStorageError) as exc:
        repo.delete_thread(other_user, other_user, thread_id)

    assert exc.value.code == "NOT_FOUND"
    assert len(repo.list_threads(user_id, user_id)) == 1


def test_stale_state_revision_cannot_overwrite(chat_context):
    repo, user_id, thread_id = chat_context
    first_request = str(uuid4())
    second_request = str(uuid4())

    repo.begin_turn(user_id, user_id, thread_id, first_request, "First")
    repo.begin_turn(user_id, user_id, thread_id, second_request, "Second")

    repo.complete_turn(
        user_id, user_id, thread_id, first_request,
        "First answer",
        {"text": "First answer"},
        {"step": 1},
        schema_version=1,
        expected_revision=0,
    )

    with pytest.raises(ChatStorageError) as exc:
        repo.complete_turn(
            user_id, user_id, thread_id, second_request,
            "Second answer",
            {"text": "Second answer"},
            {"step": 2},
            schema_version=1,
            expected_revision=0,
        )

    assert exc.value.code == "CONFLICT"
    assert repo.get_state(user_id, user_id, thread_id).revision == 1
    assert len(repo.list_messages(user_id, user_id, thread_id)) == 3
