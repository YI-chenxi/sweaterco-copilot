"""Runs P4 + actual P3 code against scripted DB-API responses, NOT a live DB."""

import hashlib
from datetime import datetime, timezone

import pytest

pytest.importorskip("app.accounts.storage.postgres")

from app.accounts.conversation.codec import encode_reply, encode_session
from app.accounts.conversation.contracts import Principal
from app.accounts.conversation.errors import ConversationError
from app.accounts.conversation.postgres_adapter import PostgresConversationRepository
from app.agent.contracts import AgentReply
from app.agent.turn_service import ChatSession

from .dbapi_double import Database
from .fakes import uid

P = Principal(uid(), uid(), uid())
THREAD, REQUEST = uid(), uid()
NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)
HASH = hashlib.sha256(b"hello").hexdigest()
REPLY, STATE = encode_reply(AgentReply("answer")), encode_session(ChatSession())


def guard(*steps):
    return [
        ("FOR SHARE OF s, w", [(P.session_id,)]),
        ("SELECT thread_id FROM chat_threads", [(THREAD,)]),
    ] + list(steps)


def request_step(row):
    return ("SELECT status, request_fingerprint, completion_fingerprint", [row] if row else [])


def test_load_borrows_one_transaction_and_retains_pending_user_message():
    database = Database(
        guard(
            ("SELECT request_id, status", [(REQUEST, "processing")]),
            ("SELECT m.message_id", [(uid(), THREAD, 1, "user", "hello", REQUEST, None, NOW)]),
            ("SELECT s.state_id", []),
        )
    )
    snapshot = PostgresConversationRepository(database.connect).load(P, THREAD)
    assert snapshot.pending.request_id == REQUEST
    assert len(snapshot.messages) == 1 and snapshot.revision == 0
    assert database.commits == 1  # P3's two internal read commits were suppressed.
    assert database.rollbacks == 0
    database.done()


def test_completed_load_preserves_p3_state_and_reply_documents():
    state_id = uid()
    database = Database(
        guard(
            ("SELECT request_id, status", []),
            (
                "SELECT m.message_id",
                [
                    (uid(), THREAD, 1, "user", "hello", REQUEST, None, NOW),
                    (uid(), THREAD, 2, "assistant", "answer", REQUEST, REPLY, NOW),
                ],
            ),
            ("SELECT s.state_id", [(state_id, THREAD, 1, STATE, 1, NOW)]),
        )
    )
    snapshot = PostgresConversationRepository(database.connect).load(P, THREAD)
    assert snapshot.state_document == STATE and snapshot.messages[1].reply_document == REPLY
    assert snapshot.revision == 1 and snapshot.pending is None
    database.done()


def begin_script():
    return guard(
        request_step(None),
        ("SELECT request_id, status", []),
        ("SELECT s.state_id", []),
        ("FOR UPDATE", [(THREAD,)]),
        ("SELECT status, request_fingerprint, created_at", []),
        ("INSERT INTO chat_turn_requests", [(NOW, NOW)]),
        ("SELECT COALESCE(MAX(sequence)", [(1,)]),
        ("INSERT INTO chat_messages", []),
    )


def test_new_reservation_calls_actual_p3_begin_and_commits_before_acquired():
    database = Database(begin_script())
    claim = PostgresConversationRepository(database.connect).begin_turn(
        P, THREAD, REQUEST, "hello", 0
    )
    assert claim.status == "acquired"
    assert database.commits == 1 and database.rollbacks == 0
    assert any("INSERT INTO chat_messages" in q for q, _ in database.calls)
    assert database.calls[0][1] == (P.session_id, P.user_id, P.workspace_id)
    database.done()


@pytest.mark.parametrize("status,claim", [("processing", "in_progress"), ("failed", "uncertain")])
def test_existing_processing_request_never_becomes_a_new_execution_claim(status, claim):
    database = Database(guard(request_step((status, HASH, None, None, None))))
    result = PostgresConversationRepository(database.connect).begin_turn(
        P, THREAD, REQUEST, "hello", 0
    )
    assert result.status == claim
    assert len(database.calls) == 3
    database.done()


def test_replaying_completed_request_never_requires_or_restores_old_state():
    assistant = uid()
    database = Database(
        guard(
            request_step(("completed", HASH, "a" * 64, 2, assistant)),
            (
                "SELECT role, content, reply_json",
                [
                    ("user", "hello", None, uid()),
                    ("assistant", "answer", REPLY, assistant),
                ],
            ),
        )
    )
    result = PostgresConversationRepository(database.connect).begin_turn(
        P, THREAD, REQUEST, "hello", 10
    )
    assert result.status == "completed"
    assert result.receipt.revision == 2 and result.receipt.state_document is None
    database.done()


@pytest.mark.parametrize(
    "completion,expected",
    [
        (None, "RECONCILIATION_REQUIRED"),
        ("invalid", "RECONCILIATION_REQUIRED"),
    ],
)
def test_legacy_request_without_valid_completion_receipt_is_not_reexecuted(completion, expected):
    database = Database(guard(request_step(("completed", HASH, completion, 1, uid()))))
    with pytest.raises(ConversationError) as error:
        PostgresConversationRepository(database.connect).begin_turn(P, THREAD, REQUEST, "hello", 0)
    assert error.value.code == expected and database.commits == 0
    database.done()


@pytest.mark.parametrize("kind", ["other_pending", "multiple_pending", "revision", "changed_text"])
def test_new_request_cannot_bypass_unresolved_work_or_revision_check(kind):
    if kind == "changed_text":
        steps = guard(request_step(("processing", "wrong-fingerprint", None, None, None)))
        expected = "CONFLICT"
    elif kind == "revision":
        steps = guard(
            request_step(None),
            ("SELECT request_id, status", []),
            ("SELECT s.state_id", [(uid(), THREAD, 1, STATE, 3, NOW)]),
        )
        expected = "CONFLICT"
    else:
        pending = [(uid(), "processing")]
        if kind == "multiple_pending":
            pending.append((uid(), "failed"))
        steps = guard(request_step(None), ("SELECT request_id, status", pending))
        expected = (
            "RECONCILIATION_REQUIRED" if kind == "multiple_pending" else "REQUEST_IN_PROGRESS"
        )
    database = Database(steps)
    with pytest.raises(ConversationError) as error:
        PostgresConversationRepository(database.connect).begin_turn(P, THREAD, REQUEST, "hello", 0)
    assert error.value.code == expected
    assert not any("INSERT" in q for q, _ in database.calls)
    database.done()


def commit_script():
    return guard(
        request_step(("processing", HASH, None, None, None)),
        ("SELECT content FROM chat_messages", [("hello",)]),
        ("FOR UPDATE", [(THREAD,)]),
        ("SELECT status FROM chat_turn_requests", [("processing",)]),
        ("SELECT revision FROM chat_agent_states", []),
        ("SELECT COALESCE(MAX(sequence)", [(2,)]),
        ("INSERT INTO chat_messages", []),
        ("INSERT INTO chat_agent_states", []),
        ("UPDATE chat_turn_requests", []),
        ("UPDATE chat_threads", []),
    )


def test_complete_calls_actual_p3_and_owns_the_single_commit():
    database = Database(commit_script())
    receipt = PostgresConversationRepository(database.connect).commit_turn(
        P,
        THREAD,
        REQUEST,
        expected_revision=0,
        reply_document=REPLY,
        state_document=STATE,
    )
    assert receipt.revision == 1 and receipt.user_text == "hello"
    assert receipt.reply_document == REPLY and receipt.state_document == STATE
    assert database.commits == 1 and database.rollbacks == 0
    database.done()


def test_commit_acknowledgement_failure_is_ambiguous_not_success():
    database = Database(commit_script())
    database.fail_commit = True
    with pytest.raises(ConversationError) as error:
        PostgresConversationRepository(database.connect).commit_turn(
            P,
            THREAD,
            REQUEST,
            expected_revision=0,
            reply_document=REPLY,
            state_document=STATE,
        )
    assert error.value.code == "STORAGE_OUTCOME_UNKNOWN"
    assert "SECRET" not in str(error.value)
    database.done()


def test_p3_write_failure_rolls_back_the_outer_transaction():
    steps = commit_script()
    steps[-2] = ("UPDATE chat_turn_requests", RuntimeError("SECRET failure"))
    database = Database(steps[:-1])
    with pytest.raises(ConversationError) as error:
        PostgresConversationRepository(database.connect).commit_turn(
            P,
            THREAD,
            REQUEST,
            expected_revision=0,
            reply_document=REPLY,
            state_document=STATE,
        )
    assert error.value.code == "STORAGE_OUTCOME_UNKNOWN"
    assert database.commits == 0 and database.rollbacks == 1
    database.done()


def test_expired_session_cannot_read_or_write_even_if_principal_was_previously_valid():
    database = Database([("FOR SHARE OF s, w", [])])
    with pytest.raises(ConversationError) as error:
        PostgresConversationRepository(database.connect).load(P, THREAD)
    assert error.value.code == "UNAUTHENTICATED" and database.commits == 0
    database.done()


def test_uncertain_cleanup_needs_ownership_but_not_a_still_valid_login():
    database = Database(
        [
            ("FOR UPDATE", [(THREAD,)]),
            ("UPDATE chat_turn_requests", []),
        ]
    )
    PostgresConversationRepository(database.connect).mark_uncertain(
        P,
        THREAD,
        REQUEST,
        "AGENT_ERROR",
    )
    assert database.commits == 1
    assert "status = 'processing'" in database.calls[-1][0]
    database.done()


@pytest.mark.parametrize(
    "operation,args,fragment",
    [
        ("create_thread", ("Title",), "INSERT INTO chat_threads"),
        ("list_threads", (), "SELECT thread_id, user_id"),
        ("rename_thread", (THREAD, "Renamed"), "UPDATE chat_threads"),
        ("delete_thread", (THREAD,), "DELETE FROM chat_threads"),
    ],
)
def test_lifecycle_delegates_to_p3_with_trusted_scope_and_one_commit(operation, args, fragment):
    row = (THREAD, P.user_id, P.workspace_id, "Title", NOW, NOW)
    steps = [("FOR SHARE OF s, w", [(P.session_id,)])]
    if operation in ("rename_thread", "delete_thread"):
        steps.append(("FOR UPDATE", [(THREAD,)]))
    steps.append((fragment, [(THREAD,)] if operation == "delete_thread" else [row]))
    database = Database(steps)
    result = PostgresConversationRepository(database.connect).lifecycle(P, operation, *args)
    assert P.user_id in database.calls[-1][1] and P.workspace_id in database.calls[-1][1]
    assert database.commits == 1 and database.rollbacks == 0
    if operation != "delete_thread":
        thread = result[0] if operation == "list_threads" else result
        assert thread.thread_id == THREAD and thread.user_id == P.user_id
    database.done()
