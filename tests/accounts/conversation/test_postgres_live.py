"""Opt-in REAL PostgreSQL tests in a newly created isolated schema.

Never reads DATABASE_URL implicitly. No production rows/migrations are modified.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from app.accounts.conversation.errors import ConversationError
from app.accounts.conversation.runtime import compose_account_backend
from tests.test_chat_integration import payload, service, task

from .fakes import FakeAgent, uid

apply_migrations = pytest.importorskip("app.accounts.storage.migrate").apply_migrations
psycopg = pytest.importorskip("psycopg")
sql = psycopg.sql

SECRET = b"only-a-test-key-not-a-deployment-key" * 2
PASSWORD = "P4 dedicated integration test phrase!"


@pytest.fixture(scope="module")
def real_factory():
    url = os.getenv("SWEATERCO_P4_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL URL not configured; real DB integration NOT verified")
    if os.getenv("SWEATERCO_P4_ALLOW_TEST_SCHEMA") != "1":
        pytest.fail("Explicit opt-in required to create an isolated test schema", pytrace=False)
    schema = "p4_test_" + uuid4().hex
    created = False

    def connect():
        try:
            connection = psycopg.connect(url, connect_timeout=10)
        except Exception:
            pytest.fail(
                "Dedicated PostgreSQL connection failed (details suppressed)", pytrace=False
            )
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        connection.commit()
        return connection

    try:
        try:
            with psycopg.connect(url, connect_timeout=10) as admin:
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
        except Exception:
            pytest.fail("Isolated test schema creation failed (details suppressed)", pytrace=False)
        assert apply_migrations(connect) == ["001", "002"]
        yield connect
    finally:
        if created:
            assert schema.startswith("p4_test_") and len(schema) == 40
            try:
                with psycopg.connect(url, connect_timeout=10) as admin:
                    admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            except Exception:
                pytest.fail(
                    "Test schema cleanup failed; check isolated p4_test schema", pytrace=False
                )


def make_backend(factory, agent=None):
    return compose_account_backend(
        agent=agent or FakeAgent(),
        connection_factory=factory,
        account_secret=SECRET,
    )


def create_user(backend):
    username = "p4_" + uuid4().hex[:18]
    backend.register(username, PASSWORD)
    return backend.login(username, PASSWORD)


def test_real_database_relogin_restores_pending_task_and_complete_evidence(real_factory):
    agent, _, executor = service(
        payload(task(order_id="ORD-057"), task("order_feasibility", category="TOPS", pieces=800)),
        payload(updates=[{"task_id": "task-002", "requested_delivery_date": "2026-04-25"}]),
    )
    backend = make_backend(real_factory, agent)
    login = create_user(backend)
    token = login.cookie.value
    thread = backend.create_thread(token, "Pending business task")
    first = backend.handle_turn(token, thread.thread_id, "ORD-057 and 800 TOPS", uid())
    assert first.reply.needs_clarification and len(executor.calls) == 1
    backend.logout(token)
    # Fresh services + new login + new DB connections. No reused UI/session ChatSession.
    reopened = make_backend(real_factory, agent)
    token = reopened.login(login.username, PASSWORD).cookie.value
    history = reopened.open_thread(token, thread.thread_id)
    assert len(history.messages) == 2 and history.session.next_task_number == 3
    result = reopened.handle_turn(token, thread.thread_id, "Delivery date 2026-04-25", uid())
    assert len(executor.calls) == 2 and result.reply.evidence and result.reply.assumptions
    assert reopened.open_thread(token, thread.thread_id).revision == 2


def test_real_database_user_isolation_and_lifecycle(real_factory):
    backend = make_backend(real_factory)
    alice, bob = create_user(backend), create_user(backend)
    a, b = alice.cookie.value, bob.cookie.value
    thread = backend.create_thread(a, "Alice only")
    assert all(t.thread_id != thread.thread_id for t in backend.list_threads(b))
    for operation in (
        lambda: backend.open_thread(b, thread.thread_id),
        lambda: backend.rename_thread(b, thread.thread_id, "stolen"),
        lambda: backend.delete_thread(b, thread.thread_id),
        lambda: backend.handle_turn(b, thread.thread_id, "steal?", uid()),
    ):
        with pytest.raises(ConversationError) as error:
            operation()
        assert error.value.code in ("FORBIDDEN", "NOT_FOUND")
    assert backend.rename_thread(a, thread.thread_id, "Renamed").title == "Renamed"
    backend.delete_thread(a, thread.thread_id)
    assert all(t.thread_id != thread.thread_id for t in backend.list_threads(a))


def test_real_database_old_replay_does_not_roll_back_later_state(real_factory):
    agent = FakeAgent()
    backend = make_backend(real_factory, agent)
    token = create_user(backend).cookie.value
    thread, request = backend.create_thread(token), uid()
    first = backend.handle_turn(token, thread.thread_id, "first", request)
    backend.handle_turn(token, thread.thread_id, "second", uid())
    replay = backend.handle_turn(token, thread.thread_id, "first", request)
    assert replay.replayed and replay.reply == first.reply and agent.calls == 2
    assert backend.open_thread(token, thread.thread_id).revision == 2
    assert backend.open_thread(token, thread.thread_id).session.next_task_number == 3


def test_real_database_concurrent_workers_do_not_execute_twice(real_factory):
    agent, entered, release = FakeAgent(), Event(), Event()

    def wait(session):
        entered.set()
        assert release.wait(15)

    agent.hook = wait
    backend = make_backend(real_factory, agent)
    other_agent = FakeAgent()
    other = make_backend(real_factory, other_agent)
    login = create_user(backend)
    token = login.cookie.value
    thread, request = backend.create_thread(token), uid()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(backend.handle_turn, token, thread.thread_id, "first", request)
        try:
            assert entered.wait(15)
            opened = other.open_thread(token, thread.thread_id)
            assert opened.pending.status == "processing" and len(opened.messages) == 1
            for text, request_id in (("first", request), ("second", uid())):
                with pytest.raises(ConversationError) as error:
                    other.handle_turn(token, thread.thread_id, text, request_id)
                assert error.value.code == "REQUEST_IN_PROGRESS"
        finally:
            release.set()
        future.result(timeout=15)
    assert agent.calls == 1 and other_agent.calls == 0


def test_real_database_failure_is_visible_and_blocks_unchecked_retry(real_factory):
    agent = FakeAgent()

    def fail(session):
        raise RuntimeError("Test agent failure")

    agent.hook = fail
    backend = make_backend(real_factory, agent)
    token = create_user(backend).cookie.value
    thread, request = backend.create_thread(token), uid()
    with pytest.raises(ConversationError):
        backend.handle_turn(token, thread.thread_id, "first", request)
    history = backend.open_thread(token, thread.thread_id)
    assert history.pending.status == "failed" and len(history.messages) == 1
    with pytest.raises(ConversationError) as error:
        backend.handle_turn(token, thread.thread_id, "first", request)
    assert error.value.code == "RECONCILIATION_REQUIRED" and agent.calls == 1


def test_real_database_commit_then_lost_ack_replays_without_execution(real_factory):
    agent = FakeAgent()
    backend = make_backend(real_factory, agent)
    token = create_user(backend).cookie.value
    thread, request = backend.create_thread(token), uid()
    commit = backend._repository.commit_turn

    def lost(*args, **kwargs):
        commit(*args, **kwargs)
        raise ConversationError("STORAGE_OUTCOME_UNKNOWN")

    backend._repository.commit_turn = lost
    with pytest.raises(ConversationError) as error:
        backend.handle_turn(token, thread.thread_id, "first", request)
    assert error.value.code == "STORAGE_OUTCOME_UNKNOWN"
    backend._repository.commit_turn = commit
    assert backend.handle_turn(token, thread.thread_id, "first", request).replayed
    assert agent.calls == 1 and backend.open_thread(token, thread.thread_id).pending is None


def test_real_database_logout_during_agent_prevents_assistant_commit(real_factory):
    agent = FakeAgent()
    backend = make_backend(real_factory, agent)
    login = create_user(backend)
    token = login.cookie.value
    thread = backend.create_thread(token)
    agent.hook = lambda session: backend.logout(token)
    with pytest.raises(ConversationError) as error:
        backend.handle_turn(token, thread.thread_id, "first", uid())
    assert error.value.code == "UNAUTHENTICATED"
    new_token = backend.login(login.username, PASSWORD).cookie.value
    history = backend.open_thread(new_token, thread.thread_id)
    assert history.pending.status == "failed" and history.revision == 0
    assert len(history.messages) == 1
