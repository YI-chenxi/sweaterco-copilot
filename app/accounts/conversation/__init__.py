"""Account Person 4: safe codecs and a provisional persistent-turn facade."""

from .codec import decode_reply, decode_session, encode_reply, encode_session
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
from .service import PersistentConversationService

__all__ = [
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
