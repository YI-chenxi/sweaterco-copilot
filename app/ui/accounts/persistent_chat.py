"""Account-scoped view and submission state for a single frontend session.

The host resolves the current server-side session handle at event time, syncs
before rendering, and clears on logout. Keep the adapter in one browser session.

Both keyboard and reviewed voice callbacks pass the rendered submission key to
queue_text. The rerun may process the returned request ID once. Retry uses a
separate one-use key; refresh only reads. Unknown results without a stored
receipt stay blocked for reconciliation while this adapter session lasts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from app.accounts.conversation import ConversationError, Principal
from app.accounts.conversation.service import MAX_TEXT_CHARACTERS

if TYPE_CHECKING:
    from app.accounts.conversation import AccountBackend, OpenedConversation, PendingTurn
    from app.agent.contracts import AgentReply
    from app.agent.turn_service import ChatSession

Phase = Literal[
    "queued",
    "sending",
    "retryable",
    "unknown",
    "processing",
    "reconciliation_required",
    "reload_required",
    "conflict",
]
_ACCESS_ERRORS = {"FORBIDDEN", "NOT_FOUND"}
_SESSION_ERRORS = {"UNAUTHENTICATED", "IDENTITY_CHANGED"}
_SEND_ERRORS: dict[str, Phase] = {
    "STORAGE_ERROR": "retryable",
    "REQUEST_IN_PROGRESS": "processing",
    "RECONCILIATION_REQUIRED": "reconciliation_required",
    "CONFLICT": "conflict",
}


@dataclass(frozen=True)
class ThreadListItem:
    thread_id: str
    title: str


@dataclass(frozen=True)
class MessageView:
    message_id: str
    request_id: str
    sequence: int
    role: str
    text: str
    reply: AgentReply | None


@dataclass(frozen=True)
class ThreadView:
    thread_id: str
    revision: int
    messages: tuple[MessageView, ...]
    agent_state: ChatSession
    pending: PendingTurn | None


@dataclass(frozen=True)
class Submission:
    thread_id: str
    request_id: str
    text: str = field(repr=False)
    phase: Phase = "queued"
    committed_revision: int | None = None
    guided: dict | None = field(default=None, repr=False)


def _thread_view(snapshot: OpenedConversation) -> ThreadView:
    return ThreadView(
        thread_id=snapshot.thread_id,
        revision=snapshot.revision,
        messages=tuple(
            MessageView(
                message.id,
                message.request_id,
                message.sequence,
                message.role,
                message.content,
                deepcopy(message.reply),
            )
            for message in snapshot.messages
        ),
        agent_state=deepcopy(snapshot.session),
        pending=snapshot.pending,
    )


class PersistentChat:
    """Standalone adapter. All persisted reads and writes go through P4."""

    def __init__(self, backend: AccountBackend) -> None:
        self._backend = backend
        self.clear()

    def clear(self) -> None:
        """Drop all account data and invalidate old callbacks, e.g. on logout."""
        self._owner: Principal | None = None
        self._scope = object()
        self._selected_id: str | None = None
        self._view: ThreadView | None = None
        self._event_key: str | None = None
        self._submissions: dict[str, Submission] = {}

    @property
    def selected(self) -> ThreadView | None:
        return self._view

    @property
    def submission(self) -> Submission | None:
        return self._submissions.get(self._selected_id) if self._selected_id else None

    def has_submission(self, thread_id: str) -> bool:
        return thread_id in self._submissions

    @property
    def submission_key(self) -> str | None:
        if self._view is None or self._view.pending is not None or self.submission is not None:
            return None
        return self._event_key

    @property
    def retry_key(self) -> str | None:
        request = self.submission
        return self._event_key if request is not None and request.phase == "retryable" else None

    def sync_session(self, handle: str | None) -> Principal | None:
        """Validate the current handle and discard state belonging to another login."""
        if not handle:
            self.clear()
            return None
        scope = self._scope
        try:
            principal = self._backend.current_user(handle)
            if not isinstance(principal, Principal):
                raise ConversationError("UNAUTHENTICATED")
        except ConversationError:
            self._check_scope(scope)
            self.clear()
            raise
        except Exception:
            self._check_scope(scope)
            self.clear()
            raise ConversationError("STORAGE_ERROR") from None

        self._check_scope(scope)
        if principal != self._owner:
            self.clear()
            self._owner = principal
        return principal

    def _require_owner(self, handle: str) -> Principal:
        principal = self.sync_session(handle)
        if principal is None:
            raise ConversationError("UNAUTHENTICATED")
        return principal

    def _check_scope(self, scope: object) -> None:
        if self._scope is not scope:
            raise ConversationError("IDENTITY_CHANGED")

    def list_threads(self, handle: str) -> tuple[ThreadListItem, ...]:
        principal = self._require_owner(handle)
        scope = self._scope
        try:
            threads = self._backend.list_threads(handle)
        except ConversationError as error:
            self._check_scope(scope)
            if error.code in _SESSION_ERRORS | _ACCESS_ERRORS:
                self.clear()
            raise
        except Exception:
            self._check_scope(scope)
            raise ConversationError("STORAGE_ERROR") from None

        self._check_scope(scope)
        if any(
            (thread.user_id, thread.workspace_id) != (principal.user_id, principal.workspace_id)
            for thread in threads
        ):
            self.clear()
            raise ConversationError("FORBIDDEN")
        return tuple(ThreadListItem(thread.thread_id, thread.title) for thread in threads)

    def open_thread(self, handle: str, thread_id: str) -> ThreadView:
        """Select a thread through P4. Selecting a thread never submits text."""
        self._require_owner(handle)
        self._select(thread_id)
        return self._load(handle, thread_id, self._scope)

    def deselect(self) -> None:
        """Drop the current view and its event key while retaining pending requests."""
        if self._selected_id is not None:
            self._select(None)

    def refresh(self, handle: str) -> ThreadView | None:
        """Read current history and resolve a local request only from stored evidence."""
        self._require_owner(handle)
        if self._selected_id is None:
            return None
        return self._load(handle, self._selected_id, self._scope)

    def _select(self, thread_id: str | None) -> None:
        self._selected_id = thread_id
        self._view = None
        self._event_key = uuid4().hex if thread_id is not None else None

    def _on_thread_error(self, thread_id: str, error: ConversationError) -> None:
        if error.code in _SESSION_ERRORS:
            self.clear()
        elif error.code in _ACCESS_ERRORS:
            self._submissions.pop(thread_id, None)
            if self._selected_id == thread_id:
                self._select(None)
        elif error.code == "RECONCILIATION_REQUIRED":
            request = self._submissions.get(thread_id)
            if request is not None and request.phase != "sending":
                self._submissions[thread_id] = replace(request, phase="reconciliation_required")

    def _load(self, handle: str, thread_id: str, scope: object) -> ThreadView:
        try:
            snapshot = self._backend.open_thread(handle, thread_id)
        except ConversationError as error:
            self._check_scope(scope)
            if self._selected_id == thread_id:
                self._view = None
            self._on_thread_error(thread_id, error)
            raise
        except Exception:
            self._check_scope(scope)
            if self._selected_id == thread_id:
                self._view = None
            raise ConversationError("STORAGE_ERROR") from None

        self._check_scope(scope)
        if self._selected_id != thread_id:
            raise ConversationError("CONFLICT")
        if snapshot.thread_id != thread_id:
            self._view = None
            raise ConversationError("INVALID_STATE")
        view = _thread_view(snapshot)
        self._reconcile(view)
        self._view = view
        return view

    def _reconcile(self, view: ThreadView) -> None:
        request = self._submissions.get(view.thread_id)
        if request is None or request.phase == "sending":
            return
        if request.phase == "queued":
            if view.pending is not None:
                self._submissions[view.thread_id] = replace(request, phase="conflict")
            return
        if view.pending is not None and view.pending.request_id == request.request_id:
            phase = (
                "processing" if view.pending.status == "processing" else "reconciliation_required"
            )
            self._submissions[view.thread_id] = replace(request, phase=phase)
            return

        messages = [
            message for message in view.messages if message.request_id == request.request_id
        ]
        if (
            len(messages) == 2
            and messages[0].role == "user"
            and messages[0].text == request.text
            and messages[1].role == "assistant"
            and messages[1].reply is not None
            and (request.committed_revision is None or view.revision >= request.committed_revision)
        ):
            del self._submissions[view.thread_id]
        elif request.phase == "conflict" and not messages and view.pending is None:
            # P4 rejected this write. A new send still requires a new user event.
            del self._submissions[view.thread_id]

    def queue_text(self, handle: str, event_key: str, text: str) -> str | None:
        """Accept one explicit send event and keep the exact user-approved text."""
        self._require_owner(handle)
        view = self._view
        if view is None or not event_key or event_key != self.submission_key:
            return None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
            raise ConversationError("INVALID_ARGUMENT")
        request = Submission(view.thread_id, str(uuid4()), text)
        self._submissions[view.thread_id] = request
        self._event_key = uuid4().hex
        return request.request_id

    def process_queued(self, handle: str, request_id: str) -> ThreadView | None:
        """Execute an accepted event once. Calling again on rerun has no write effect."""
        self._require_owner(handle)
        request = self.submission
        if request is None or request.request_id != request_id or request.phase != "queued":
            return None
        if self._view is None or self._view.pending is not None:
            return None
        return self._send(handle, request)

    def queue_guided(self, handle, event_key, workflow, values, language):
        from app.accounts.conversation.guided import prepare_guided

        prepared = prepare_guided(workflow, values, language)
        request_id = self.queue_text(handle, event_key, prepared.title)
        if request_id is not None:
            self._submissions[self._selected_id] = replace(
                self.submission, guided=prepared.document()
            )
        return request_id

    def retry(self, handle: str, request_id: str, event_key: str) -> ThreadView | None:
        """Explicitly retry a recoverable storage error, after a read-only refresh."""
        self._require_owner(handle)
        request = self.submission
        if (
            request is None
            or request.request_id != request_id
            or not event_key
            or event_key != self.retry_key
        ):
            return None

        self._event_key = uuid4().hex
        view = self._load(handle, request.thread_id, self._scope)
        if self.submission != request or view.pending is not None:
            return view
        if any(message.request_id == request.request_id for message in view.messages):
            self._submissions[request.thread_id] = replace(request, phase="reconciliation_required")
            return view
        return self._send(handle, request)

    def _send(self, handle: str, request: Submission) -> ThreadView:
        scope = self._scope
        thread_id = request.thread_id
        self._submissions[thread_id] = replace(request, phase="sending")
        try:
            if request.guided is not None:
                saved = self._backend.handle_guided_turn(
                    handle,
                    thread_id,
                    request.guided["workflow"],
                    deepcopy(request.guided["values"]),
                    request.guided["language"],
                    request.request_id,
                )
            else:
                saved = self._backend.handle_turn(
                    handle, thread_id, request.text, request.request_id
                )
        except ConversationError as error:
            self._check_scope(scope)
            if error.code in _SESSION_ERRORS | _ACCESS_ERRORS:
                self._on_thread_error(thread_id, error)
            elif error.code == "INVALID_ARGUMENT":
                del self._submissions[thread_id]
            else:
                phase = _SEND_ERRORS.get(error.code, "unknown")
                self._submissions[thread_id] = replace(request, phase=phase)
            raise
        except Exception:
            self._check_scope(scope)
            self._submissions[thread_id] = replace(request, phase="unknown")
            raise ConversationError("STORAGE_OUTCOME_UNKNOWN") from None

        self._check_scope(scope)
        if saved.thread_id != thread_id or saved.request_id != request.request_id:
            self._submissions[thread_id] = replace(request, phase="unknown")
            raise ConversationError("INVALID_STATE")
        self._submissions[thread_id] = replace(
            request, phase="reload_required", committed_revision=saved.committed_revision
        )
        # Both a fresh receipt and a replay may be older than the current thread.
        if self._selected_id != thread_id:
            raise ConversationError("CONFLICT")
        view = self._load(handle, thread_id, scope)
        if thread_id in self._submissions:
            raise ConversationError("STORAGE_ERROR")
        return view
