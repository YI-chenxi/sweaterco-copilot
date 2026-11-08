"""Account Person 4: safe codecs and a provisional persistent-turn facade."""

from .codec import decode_reply, decode_session, encode_reply, encode_session
from .contracts import (
    ConversationRepository,
    ConversationSnapshot,
    DisplayMessage,
    IdentityProvider,
    OpenedConversation,
    PendingTurn,
    Principal,
    SavedTurn,
    StoredMessage,
    TurnAgent,
    TurnClaim,
    TurnReceipt,
)
from .errors import ConversationError
from .identity_adapter import SessionIdentityAdapter
from .postgres_adapter import PostgresConversationRepository
from .runtime import AccountBackend, LoginResult, build_account_backend, compose_account_backend
from .service import PersistentConversationService

__all__ = [
    "AccountBackend",
    "LoginResult",
    "PendingTurn",
    "PostgresConversationRepository",
    "SessionIdentityAdapter",
    "build_account_backend",
    "compose_account_backend",
    "ConversationError",
    "ConversationRepository",
    "ConversationSnapshot",
    "DisplayMessage",
    "IdentityProvider",
    "OpenedConversation",
    "PersistentConversationService",
    "Principal",
    "SavedTurn",
    "StoredMessage",
    "TurnAgent",
    "TurnClaim",
    "TurnReceipt",
    "decode_reply",
    "decode_session",
    "encode_reply",
    "encode_session",
]
