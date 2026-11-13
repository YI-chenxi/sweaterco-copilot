"""P4-local PROVISIONAL adapter contracts, not a replacement for P2/P3 contracts."""

from dataclasses import dataclass
from typing import Protocol

from app.agent.contracts import AgentReply
from app.agent.turn_service import ChatSession, TurnResult


@dataclass(frozen=True)
class Principal:
    """Only IdentityProvider may derive this from a trusted login session."""

    user_id: str
    workspace_id: str
    session_id: str


@dataclass(frozen=True)
class StoredMessage:
    id: str
    request_id: str
    sequence: int
    role: str
    content: str
    reply_document: dict | None = None


@dataclass(frozen=True)
class PendingTurn:
    request_id: str
    status: str  # processing | failed (requires reconciliation, not safe to retry)


@dataclass(frozen=True)
class ConversationSnapshot:
    thread_id: str
    owner_user_id: str
    workspace_id: str
    revision: int
    messages: tuple[StoredMessage, ...] = ()
    state_document: dict | None = None
    pending: PendingTurn | None = None


@dataclass(frozen=True)
class TurnReceipt:
    thread_id: str
    owner_user_id: str
    workspace_id: str
    request_id: str
    user_text: str
    base_revision: int
    revision: int
    reply_document: dict
    state_document: dict | None  # P3 historical replay has no historical state snapshot.


@dataclass(frozen=True)
class TurnClaim:
    status: str  # acquired | completed | in_progress | uncertain
    receipt: TurnReceipt | None = None


@dataclass(frozen=True)
class DisplayMessage:
    id: str
    request_id: str
    sequence: int
    role: str
    content: str
    reply: AgentReply | None


@dataclass(frozen=True)
class OpenedConversation:
    thread_id: str
    revision: int
    messages: tuple[DisplayMessage, ...]
    session: ChatSession  # Detached read snapshot, never a caller-supplied update.
    pending: PendingTurn | None = None


@dataclass(frozen=True)
class SavedTurn:
    """No writable session is returned; UI reloads current history after saving/replay."""

    thread_id: str
    request_id: str
    committed_revision: int
    reply: AgentReply
    replayed: bool


class IdentityProvider(Protocol):
    def require_principal(self, session_handle: object) -> Principal:
        """Validate expiry/revocation and workspace membership on EVERY call.

        Never construct this result from editable UI/model user_id fields.
        Raise ConversationError on denial. No default allow policy exists.
        """
        ...


class TurnAgent(Protocol):
    def handle_turn(self, text: str, session: ChatSession) -> TurnResult: ...


class ConversationRepository(Protocol):
    """P3 adapter must enforce ownership, atomicity and persistent request deduplication.

    All returned values are detached. New threads have revision=0, no messages and
    no state document. A completed turn increments revision exactly once.
    """

    def load(self, principal: Principal, thread_id: str) -> ConversationSnapshot:
        """Consistent message/state/revision snapshot; deny deleted/foreign threads."""
        ...

    def begin_turn(
        self,
        principal: Principal,
        thread_id: str,
        request_id: str,
        user_text: str,
        expected_revision: int,
    ) -> TurnClaim:
        """Atomically reserve the request and serialize new turns on this thread.

        Match existing request IDs/text FIRST, returning their original receipt.
        Otherwise compare revision and deny any outstanding/uncertain request.
        Commit this reservation before returning acquired. Do not hold a DB
        transaction open across the agent call.
        """
        ...

    def commit_turn(
        self,
        principal: Principal,
        thread_id: str,
        request_id: str,
        *,
        expected_revision: int,
        reply_document: dict,
        state_document: dict,
    ) -> TurnReceipt:
        """Atomically save user+assistant messages, state, revision and replay receipt.

        Confirm reservation, ownership, unchanged revision and thread existence.
        Repeating a committed request returns its original receipt. Do not create a
        deleted thread. Once committed, a later error must not destroy the receipt.
        """
        ...

    def mark_uncertain(
        self,
        principal: Principal,
        thread_id: str,
        request_id: str,
        error_code: str,
    ) -> None:
        """Retain unresolved reservation; never reset or overwrite a completed receipt.

        Another request ID must not bypass the unresolved request. This method is
        not an automatic retry/recovery API. Never store raw exception messages.
        """
        ...
