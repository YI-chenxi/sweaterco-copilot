"""P4 orchestration over replaceable identity/storage adapters.

No credentials, database connection, UI state, or user session is cached here.
"""

from uuid import UUID

from app.agent.turn_service import ChatSession, TurnResult

from .codec import decode_reply, decode_session, document_copy, encode_reply, encode_session
from .contracts import (
    ConversationRepository,
    ConversationSnapshot,
    DisplayMessage,
    IdentityProvider,
    OpenedConversation,
    Principal,
    SavedTurn,
    StoredMessage,
    TurnAgent,
    TurnClaim,
    TurnReceipt,
)
from .errors import ConversationError

MAX_TEXT_CHARACTERS = 32_000


def _deny(code="INVALID_STATE"):
    raise ConversationError(code)


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            _deny("INVALID_ARGUMENT")
    except (ValueError, AttributeError):
        _deny("INVALID_ARGUMENT")


def _revision(value):
    if type(value) is not int or value < 0:
        _deny()


class PersistentConversationService:
    """Stateless facade; P2 validates identity, P3 owns durable transactions."""

    def __init__(
        self,
        agent: TurnAgent,
        repository: ConversationRepository | None = None,
        identity: IdentityProvider | None = None,
    ):
        self._agent = agent
        self._repository = repository
        self._identity = identity

    def _principal(self, handle):
        if self._identity is None:
            _deny("UNAUTHENTICATED")
        try:
            principal = self._identity.require_principal(handle)
        except ConversationError:
            raise
        except Exception:
            raise ConversationError("UNAUTHENTICATED") from None
        if not isinstance(principal, Principal):
            _deny("UNAUTHENTICATED")
        if any(
            type(value) is not str or not value.strip() or len(value) > 200
            for value in (principal.user_id, principal.workspace_id, principal.session_id)
        ):
            _deny("UNAUTHENTICATED")
        return principal

    def _revalidate(self, handle, principal):
        if self._principal(handle) != principal:
            _deny("IDENTITY_CHANGED")

    def _storage(self, method, *args, unknown=False, **kwargs):
        if self._repository is None:
            _deny("STORAGE_ERROR")
        try:
            return getattr(self._repository, method)(*args, **kwargs)
        except ConversationError:
            raise
        except Exception:
            code = "STORAGE_OUTCOME_UNKNOWN" if unknown else "STORAGE_ERROR"
            raise ConversationError(code) from None

    @staticmethod
    def _owned(record, principal, thread_id):
        if (
            record.thread_id != thread_id
            or record.owner_user_id != principal.user_id
            or record.workspace_id != principal.workspace_id
        ):
            _deny("FORBIDDEN")

    def _load(self, principal, thread_id):
        snapshot = self._storage("load", principal, thread_id)
        if not isinstance(snapshot, ConversationSnapshot):
            _deny()
        self._owned(snapshot, principal, thread_id)
        _revision(snapshot.revision)
        if type(snapshot.messages) is not tuple:
            _deny()
        # Every committed turn is a user/assistant pair in the proposed P3 contract.
        if len(snapshot.messages) != snapshot.revision * 2:
            _deny()
        messages = []
        ids = set()
        requests = set()
        previous_request = None
        for index, item in enumerate(snapshot.messages):
            if not isinstance(item, StoredMessage):
                _deny()
            try:
                _uuid(item.id)
                _uuid(item.request_id)
            except ConversationError:
                _deny()
            if item.id in ids or type(item.sequence) is not int or item.sequence != index + 1:
                _deny()
            ids.add(item.id)
            role = "user" if index % 2 == 0 else "assistant"
            if item.role != role or type(item.content) is not str:
                _deny()
            reply = None
            if role == "user":
                if item.request_id in requests or item.reply_document is not None:
                    _deny()
                requests.add(item.request_id)
                previous_request = item.request_id
            else:
                if item.request_id != previous_request or item.reply_document is None:
                    _deny()
                reply = decode_reply(item.reply_document)
                if reply.text != item.content:
                    _deny()
            messages.append(
                DisplayMessage(
                    item.id,
                    item.request_id,
                    item.sequence,
                    item.role,
                    item.content,
                    reply,
                )
            )
        if snapshot.state_document is None:
            if snapshot.revision or snapshot.messages:
                _deny()
            session = ChatSession()
        else:
            session = decode_session(snapshot.state_document)
        return snapshot, tuple(messages), session

    def open_thread(self, session_handle: object, thread_id: str) -> OpenedConversation:
        principal = self._principal(session_handle)
        _uuid(thread_id)
        snapshot, messages, session = self._load(principal, thread_id)
        self._revalidate(session_handle, principal)
        return OpenedConversation(thread_id, snapshot.revision, messages, session)

    def _receipt(self, receipt, principal, thread_id, request_id, text):
        if not isinstance(receipt, TurnReceipt):
            _deny()
        self._owned(receipt, principal, thread_id)
        _revision(receipt.base_revision)
        _revision(receipt.revision)
        if (
            receipt.request_id != request_id
            or receipt.user_text != text
            or receipt.revision != receipt.base_revision + 1
        ):
            _deny()
        reply = decode_reply(receipt.reply_document)
        decode_session(receipt.state_document)
        return reply

    def _uncertain(self, principal, thread_id, request_id, code):
        try:
            self._storage("mark_uncertain", principal, thread_id, request_id, code)
        except Exception:
            # begin_turn already persisted the reservation. Never release/re-execute it.
            pass

    def handle_turn(
        self,
        session_handle: object,
        thread_id: str,
        text: str,
        request_id: str,
    ) -> SavedTurn:
        principal = self._principal(session_handle)
        _uuid(thread_id)
        _uuid(request_id)
        if type(text) is not str or not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
            _deny("INVALID_ARGUMENT")
        snapshot, _, session = self._load(principal, thread_id)
        self._revalidate(session_handle, principal)
        # A lost reservation acknowledgement may still have persisted the claim.
        # Retrying this SAME request ID may query it, but must never take it over.
        claim = self._storage(
            "begin_turn",
            principal,
            thread_id,
            request_id,
            text,
            snapshot.revision,
            unknown=True,
        )
        if not isinstance(claim, TurnClaim):
            _deny()
        if claim.status == "completed":
            reply = self._receipt(claim.receipt, principal, thread_id, request_id, text)
            self._revalidate(session_handle, principal)
            return SavedTurn(thread_id, request_id, claim.receipt.revision, reply, True)
        if claim.status == "in_progress":
            _deny("REQUEST_IN_PROGRESS")
        if claim.status == "uncertain":
            _deny("RECONCILIATION_REQUIRED")
        if claim.status != "acquired" or claim.receipt is not None:
            _deny()
        try:
            self._revalidate(session_handle, principal)
            try:
                result = self._agent.handle_turn(text, session)
            except Exception:
                raise ConversationError("AGENT_ERROR") from None
            if not isinstance(result, TurnResult):
                _deny("AGENT_ERROR")
            reply_document = encode_reply(result.reply)
            state_document = encode_session(result.session)
            self._revalidate(session_handle, principal)
            receipt = self._storage(
                "commit_turn",
                principal,
                thread_id,
                request_id,
                expected_revision=snapshot.revision,
                reply_document=reply_document,
                state_document=state_document,
                unknown=True,
            )
            reply = self._receipt(receipt, principal, thread_id, request_id, text)
            if (
                receipt.base_revision != snapshot.revision
                or document_copy(receipt.reply_document) != reply_document
                or document_copy(receipt.state_document) != state_document
            ):
                _deny()
            self._revalidate(session_handle, principal)
            return SavedTurn(thread_id, request_id, receipt.revision, reply, False)
        except ConversationError as error:
            self._uncertain(principal, thread_id, request_id, error.code)
            raise
        except Exception:
            self._uncertain(principal, thread_id, request_id, "AGENT_ERROR")
            raise ConversationError("AGENT_ERROR") from None
