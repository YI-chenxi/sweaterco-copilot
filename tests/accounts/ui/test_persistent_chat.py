"""Offline adapter tests with P4-shaped account and turn responses."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.accounts.conversation import (
    ConversationError,
    DisplayMessage,
    OpenedConversation,
    PendingTurn,
    Principal,
    SavedTurn,
)
from app.accounts.storage.models import ChatThread
from app.agent.contracts import AgentReply, Evidence
from app.agent.turn_service import ChatSession
from app.ui.accounts.persistent_chat import PersistentChat, ThreadListItem

_ALICE = Principal("alice", "workspace-a", "session-a")
_BOB = Principal("bob", "workspace-b", "session-b")
_NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
_TEXT = "  ORD-057 不要发 20 pieces before 2026-12-01\n"
_EVIDENCE = Evidence("orders.csv", "ORD-057", "20 pieces")


def _id():
    return str(uuid4())


def _pair(request_id, text=_TEXT, *, sequence=1):
    reply = AgentReply("答复", [_EVIDENCE])
    return (
        DisplayMessage(_id(), request_id, sequence, "user", text, None),
        DisplayMessage(_id(), request_id, sequence + 1, "assistant", reply.text, reply),
    )


class FakeAccountBackend:
    def __init__(self):
        self.identities = {"alice": _ALICE, "bob": _BOB}
        self.owners = {}
        self.snapshots = {}
        self.titles = {}
        self.sends = []
        self.reads = []
        self.replayed = False
        self.on_send = None
        self.on_read = None

    def add_thread(self, owner=_ALICE, title="保存的标题"):
        thread_id = _id()
        self.owners[thread_id] = owner
        self.titles[thread_id] = title
        self.snapshots[thread_id] = OpenedConversation(thread_id, 0, (), ChatSession())
        return thread_id

    def current_user(self, handle):
        if handle not in self.identities:
            raise ConversationError("UNAUTHENTICATED")
        return self.identities[handle]

    def list_threads(self, handle):
        principal = self.current_user(handle)
        return [
            ChatThread(
                thread_id, owner.user_id, owner.workspace_id, self.titles[thread_id], _NOW, _NOW
            )
            for thread_id, owner in self.owners.items()
            if (owner.user_id, owner.workspace_id) == (principal.user_id, principal.workspace_id)
        ]

    def _check_thread(self, handle, thread_id):
        principal = self.current_user(handle)
        owner = self.owners.get(thread_id)
        if owner is None or (owner.user_id, owner.workspace_id) != (
            principal.user_id,
            principal.workspace_id,
        ):
            raise ConversationError("NOT_FOUND")

    def open_thread(self, handle, thread_id):
        self._check_thread(handle, thread_id)
        self.reads.append((handle, thread_id))
        if self.on_read is not None:
            self.on_read()
        return self.snapshots[thread_id]

    def handle_turn(self, handle, thread_id, text, request_id):
        self._check_thread(handle, thread_id)
        self.sends.append((handle, thread_id, text, request_id))
        if self.on_send is not None:
            return self.on_send(handle, thread_id, text, request_id)
        return self.save(handle, thread_id, text, request_id)

    def save(self, handle, thread_id, text, request_id):
        snapshot = self.snapshots[thread_id]
        messages = _pair(request_id, text, sequence=len(snapshot.messages) + 1)
        self.snapshots[thread_id] = replace(
            snapshot, revision=snapshot.revision + 1, messages=snapshot.messages + messages
        )
        return SavedTurn(
            thread_id, request_id, snapshot.revision + 1, messages[1].reply, self.replayed
        )


@pytest.fixture
def chat():
    backend = FakeAccountBackend()
    thread_id = backend.add_thread()
    adapter = PersistentChat(backend)
    adapter.open_thread("alice", thread_id)
    return backend, adapter, thread_id


def _raise(code):
    raise ConversationError(code)


def _queue(adapter, text=_TEXT):
    request_id = adapter.queue_text("alice", adapter.submission_key, text)
    assert request_id == str(UUID(request_id))
    return request_id


def test_list_and_snapshot_preserve_ids_original_text_evidence_and_detached_agent_state(chat):
    backend, adapter, thread_id = chat
    backend.add_thread(_BOB)
    assert adapter.list_threads("alice") == (ThreadListItem(thread_id, "保存的标题"),)
    messages = _pair(_id()) + _pair(_id(), "Another request", sequence=3)
    messages[3].reply.evidence[:] = [Evidence("production.csv", "row-8", "other evidence")]
    state = ChatSession(active_task_ids=["task-1"], next_task_number=2)
    backend.snapshots[thread_id] = OpenedConversation(thread_id, 2, messages, state)

    view = adapter.refresh("alice")
    assert (view.thread_id, view.revision) == (thread_id, 2)
    assert [item.message_id for item in view.messages] == [item.id for item in messages]
    assert [item.request_id for item in view.messages] == [item.request_id for item in messages]
    assert [item.sequence for item in view.messages] == [1, 2, 3, 4]
    assert view.messages[0].text == _TEXT and view.messages[0].reply is None
    assert view.messages[1].reply.evidence == [_EVIDENCE]
    assert view.messages[3].reply.evidence == messages[3].reply.evidence
    assert view.agent_state == state and view.agent_state is not state
    view.agent_state.active_task_ids.clear()
    view.messages[1].reply.evidence.clear()
    assert state.active_task_ids == ["task-1"]
    assert messages[1].reply.evidence == [_EVIDENCE]
    assert not backend.sends


@pytest.mark.parametrize("replayed", [False, True])
def test_send_uses_authoritative_thread_and_drops_repeat_events(chat, replayed):
    backend, adapter, thread_id = chat
    event_key = adapter.submission_key
    request_id = _queue(adapter)
    assert adapter.queue_text("alice", event_key, _TEXT) is None
    assert adapter.selected.messages == ()
    assert not backend.sends

    def send(*args):
        # A rerun cannot execute while the first call is still in flight.
        assert adapter.process_queued("alice", request_id) is None
        backend.replayed = replayed
        saved = backend.save(*args)
        if replayed:
            backend.save("alice", thread_id, "Newer committed turn", _id())
        return replace(saved, reply=AgentReply("render only from the snapshot"))

    backend.on_send = send
    view = adapter.process_queued("alice", request_id)
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)]
    assert backend.reads == [("alice", thread_id)] * 2
    assert view.revision == (2 if replayed else 1)
    assert view.messages[1].reply.text == "答复"
    assert adapter.submission is None
    assert adapter.process_queued("alice", request_id) is None
    assert adapter.queue_text("alice", event_key, _TEXT) is None
    assert _queue(adapter) != request_id


def test_explicit_retry_keeps_id_and_original_text_and_deduplicates_retry_event(chat):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)
    backend.on_send = lambda *_: _raise("STORAGE_ERROR")
    with pytest.raises(ConversationError, match="storage is unavailable"):
        adapter.process_queued("alice", request_id)
    assert adapter.submission.phase == "retryable"
    assert adapter.process_queued("alice", request_id) is None
    retry_key = adapter.retry_key
    with pytest.raises(ConversationError, match="storage is unavailable"):
        adapter.retry("alice", request_id, retry_key)
    assert adapter.retry("alice", request_id, retry_key) is None

    backend.on_send = None
    view = adapter.retry("alice", request_id, adapter.retry_key)
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)] * 3
    assert view.messages[0].text == _TEXT
    assert adapter.submission is None


def test_unknown_commit_is_resolved_by_read_only_refresh(chat):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)

    def commit_then_disconnect(*args):
        backend.save(*args)
        _raise("STORAGE_OUTCOME_UNKNOWN")

    backend.on_send = commit_then_disconnect
    with pytest.raises(ConversationError, match="outcome is unknown"):
        adapter.process_queued("alice", request_id)
    assert adapter.submission.phase == "unknown"
    assert adapter.retry("alice", request_id, "old-event") is None
    assert adapter.refresh("alice").messages[0].request_id == request_id
    assert adapter.submission is None
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)]


@pytest.mark.parametrize(
    ("error", "phase"),
    [
        ("STORAGE_OUTCOME_UNKNOWN", "unknown"),
        ("REQUEST_IN_PROGRESS", "processing"),
        ("RECONCILIATION_REQUIRED", "reconciliation_required"),
        ("AGENT_ERROR", "unknown"),
    ],
)
def test_missing_receipt_does_not_trigger_new_execution(chat, error, phase):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)
    backend.on_send = lambda *_: _raise(error)
    with pytest.raises(ConversationError) as failure:
        adapter.process_queued("alice", request_id)
    assert failure.value.code == error
    assert adapter.refresh("alice").messages == ()
    assert adapter.submission.phase == phase
    assert adapter.submission_key is None
    assert adapter.retry_key is None
    assert adapter.process_queued("alice", request_id) is None
    assert adapter.retry("alice", request_id, "old-event") is None
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)]


@pytest.mark.parametrize(
    ("status", "phase"),
    [
        ("processing", "processing"),
        ("failed", "reconciliation_required"),
    ],
)
def test_persisted_pending_tail_blocks_retry_and_new_send(chat, status, phase):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)
    backend.on_send = lambda *_: _raise("STORAGE_OUTCOME_UNKNOWN")
    with pytest.raises(ConversationError):
        adapter.process_queued("alice", request_id)
    snapshot = backend.snapshots[thread_id]
    backend.snapshots[thread_id] = replace(
        snapshot,
        pending=PendingTurn(request_id, status),
        messages=_pair(request_id)[:1],
    )
    view = adapter.refresh("alice")
    assert view.pending.status == status
    assert view.messages[0].text == _TEXT
    assert view.messages[0].reply is None
    assert adapter.submission.phase == phase
    assert adapter.submission_key is None

    reopened = PersistentChat(backend)
    assert reopened.open_thread("alice", thread_id).pending == view.pending
    assert reopened.submission_key is None
    assert len(backend.sends) == 1


def test_saved_turn_with_failed_reload_never_sends_again(chat):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)
    backend.on_read = lambda: _raise("STORAGE_ERROR")
    with pytest.raises(ConversationError, match="storage is unavailable"):
        adapter.process_queued("alice", request_id)
    assert adapter.submission.phase == "reload_required"
    assert adapter.selected is None
    assert adapter.retry("alice", request_id, "old-event") is None
    assert adapter.process_queued("alice", request_id) is None

    backend.on_read = None
    assert adapter.refresh("alice").messages[0].request_id == request_id
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)]


def test_account_switch_removes_pending_text_snapshot_and_stale_events(chat):
    backend, adapter, thread_id = chat
    event_key = adapter.submission_key
    request_id = _queue(adapter)
    bob_thread = backend.add_thread(_BOB)
    adapter.open_thread("bob", bob_thread)
    assert adapter.queue_text("bob", event_key, _TEXT) is None
    assert adapter.process_queued("bob", request_id) is None
    assert adapter.selected.thread_id == bob_thread
    adapter.open_thread("alice", thread_id)
    assert adapter.submission is None
    assert adapter.queue_text("alice", event_key, _TEXT) is None
    assert not backend.sends


@pytest.mark.parametrize("change", ["logout", "revoked", "new_login"])
def test_clear_on_logout_expiry_or_new_session_for_same_user(chat, change):
    backend, adapter, thread_id = chat
    event_key = adapter.submission_key
    request_id = _queue(adapter)
    if change == "logout":
        adapter.clear()
    elif change == "revoked":
        del backend.identities["alice"]
        with pytest.raises(ConversationError) as error:
            adapter.process_queued("alice", request_id)
        assert error.value.code == "UNAUTHENTICATED"
        backend.identities["alice"] = _ALICE
    else:
        backend.identities["alice"] = replace(_ALICE, session_id="new-login")
        assert adapter.process_queued("alice", request_id) is None
    assert adapter.selected is None and adapter.submission is None
    adapter.open_thread("alice", thread_id)
    assert adapter.queue_text("alice", event_key, _TEXT) is None
    assert not backend.sends


def test_session_expiry_returned_during_send_clears_state(chat):
    backend, adapter, _ = chat
    request_id = _queue(adapter)
    backend.on_send = lambda *_: _raise("IDENTITY_CHANGED")
    with pytest.raises(ConversationError) as error:
        adapter.process_queued("alice", request_id)
    assert error.value.code == "IDENTITY_CHANGED"
    assert adapter.selected is None
    assert adapter.submission is None


def test_in_flight_result_cannot_repopulate_state_after_logout(chat):
    backend, adapter, _ = chat
    request_id = _queue(adapter)

    def send(*args):
        saved = backend.save(*args)
        adapter.clear()
        return saved

    backend.on_send = send
    with pytest.raises(ConversationError) as error:
        adapter.process_queued("alice", request_id)
    assert error.value.code == "IDENTITY_CHANGED"
    assert adapter.selected is None and adapter.submission is None
    assert len(backend.reads) == 1


def test_switching_threads_cannot_send_old_composer_event_to_new_thread(chat):
    backend, adapter, thread_id = chat
    event_key = adapter.submission_key
    request_id = _queue(adapter)
    other = backend.add_thread()
    adapter.open_thread("alice", other)
    assert adapter.queue_text("alice", event_key, _TEXT) is None
    assert adapter.process_queued("alice", request_id) is None
    adapter.open_thread("alice", thread_id)
    assert adapter.process_queued("alice", request_id).thread_id == thread_id
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)]


def test_forbidden_snapshot_never_remains_selected(chat):
    backend, adapter, thread_id = chat
    other = backend.add_thread(_BOB)
    with pytest.raises(ConversationError) as error:
        adapter.open_thread("alice", other)
    assert error.value.code == "NOT_FOUND"
    assert adapter.selected is None and adapter.submission_key is None
    assert adapter.open_thread("alice", thread_id).thread_id == thread_id


def test_blank_text_does_not_consume_event(chat):
    backend, adapter, _ = chat
    event_key = adapter.submission_key
    with pytest.raises(ConversationError) as error:
        adapter.queue_text("alice", event_key, " \n ")
    assert error.value.code == "INVALID_ARGUMENT"
    assert adapter.submission_key == event_key
    assert adapter.submission is None and not backend.sends


def test_retry_reads_first_and_stops_if_turn_is_already_saved(chat):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)

    def save_before_read_failure(*args):
        backend.save(*args)
        _raise("STORAGE_ERROR")

    backend.on_send = save_before_read_failure
    with pytest.raises(ConversationError, match="storage is unavailable"):
        adapter.process_queued("alice", request_id)

    view = adapter.retry("alice", request_id, adapter.retry_key)
    assert view.messages[0].request_id == request_id
    assert backend.sends == [("alice", thread_id, _TEXT, request_id)]
    assert adapter.submission is None


def test_queue_is_not_sent_after_a_read_reports_another_pending_turn(chat):
    backend, adapter, thread_id = chat
    request_id = _queue(adapter)
    other = _id()
    before = backend.snapshots[thread_id]
    backend.snapshots[thread_id] = replace(
        before, pending=PendingTurn(other, "processing"), messages=_pair(other)[:1]
    )

    assert adapter.refresh("alice").pending.request_id == other
    assert adapter.process_queued("alice", request_id) is None
    backend.snapshots[thread_id] = before
    adapter.refresh("alice")
    assert adapter.process_queued("alice", request_id) is None
    assert not backend.sends


def test_reconciliation_error_during_retry_read_disables_further_retry(chat):
    backend, adapter, _ = chat
    request_id = _queue(adapter)
    backend.on_send = lambda *_: _raise("STORAGE_ERROR")
    with pytest.raises(ConversationError, match="storage is unavailable"):
        adapter.process_queued("alice", request_id)

    backend.on_read = lambda: _raise("RECONCILIATION_REQUIRED")
    with pytest.raises(ConversationError, match="outcome must be checked"):
        adapter.retry("alice", request_id, adapter.retry_key)

    assert adapter.selected is None
    assert adapter.submission.phase == "reconciliation_required"
    assert adapter.retry_key is None and adapter.submission_key is None
    assert len(backend.sends) == 1


def test_unexpected_send_error_is_safe_and_outcome_unknown(chat):
    backend, adapter, _ = chat
    request_id = _queue(adapter)

    def disconnect(*_):
        raise RuntimeError("private diagnostic")

    backend.on_send = disconnect
    with pytest.raises(ConversationError) as error:
        adapter.process_queued("alice", request_id)
    assert error.value.code == "STORAGE_OUTCOME_UNKNOWN"
    assert "private diagnostic" not in str(error.value)
    assert adapter.submission.phase == "unknown"
    assert adapter.retry_key is None
