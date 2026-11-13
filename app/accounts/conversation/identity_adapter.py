"""Translate actual P1/P2 failures and verified P2 sessions into P4 contracts."""

from .contracts import Principal
from .errors import ConversationError

_CODE_MAP = {"INVALID_SESSION": "UNAUTHENTICATED", "UNAUTHORIZED": "FORBIDDEN"}
_PUBLIC = {
    "INVALID_USERNAME",
    "INVALID_PASSWORD",
    "USERNAME_TAKEN",
    "INVALID_CREDENTIALS",
    "RATE_LIMITED",
    "STORAGE_ERROR",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "CONFLICT",
}


def domain_call(function, *args, **kwargs):
    """No original exception/cause, database URL, hash or password escapes this boundary."""
    try:
        return function(*args, **kwargs)
    except ConversationError:
        raise
    except Exception as error:
        code = getattr(error, "code", None)
        safe = _CODE_MAP.get(code, code if code in _PUBLIC else "STORAGE_ERROR")
        raise ConversationError(safe) from None


class SessionIdentityAdapter:
    def __init__(self, sessions, authorization):
        self.sessions = sessions
        self.authorization = authorization

    def require_principal(self, session_handle: object) -> Principal:
        if type(session_handle) is not str or not session_handle or len(session_handle) > 4096:
            raise ConversationError("UNAUTHENTICATED")
        resolve_authorized = getattr(self.sessions, "resolve_authorized_current_user", None)
        if resolve_authorized is not None:
            context = domain_call(resolve_authorized, session_handle)
        else:
            context = domain_call(self.sessions.resolve_current_user, session_handle)
            allowed = domain_call(
                self.authorization.can_access_workspace,
                context.user_id,
                context.workspace_id,
            )
            if allowed is not True:
                raise ConversationError("FORBIDDEN")
        return Principal(context.user_id, context.workspace_id, context.session_id)
