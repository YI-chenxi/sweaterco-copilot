"""Account composition for P5. Does not import Streamlit or create browser cookies."""

import hashlib
import hmac
from dataclasses import dataclass, field
from uuid import UUID

from .errors import ConversationError
from .identity_adapter import SessionIdentityAdapter, domain_call
from .postgres_adapter import PostgresConversationRepository
from .service import PersistentConversationService, _uuid


@dataclass(frozen=True)
class LoginResult:
    user_id: str
    username: str
    session_id: str
    workspace_id: str
    cookie: object = field(repr=False)  # One-time sensitive browser-cookie instructions.


class AccountBackend:
    """One UI-facing entry point; caller supplies a cookie token, never identity IDs."""

    def __init__(self, auth, sessions, identity, repository, agent):
        self._auth = auth
        self._sessions = sessions
        self._identity = identity
        self._repository = repository
        self._conversation = PersistentConversationService(agent, repository, identity)

    def register(self, username: str, password: str):
        """Registration commits an account; it does not silently create a login."""
        return domain_call(self._auth.register, username, password)

    def login(self, username: str, password: str) -> LoginResult:
        verified = domain_call(self._auth.authenticate, username, password)
        try:
            user_id = str(UUID(verified.user_id))
        except (TypeError, ValueError, AttributeError):
            raise ConversationError("INVALID_STATE") from None
        # P3 migration 001 provisions private workspace_id = user_id on registration.
        # This is a server-owned mapping, not a selector from a form/model/URL.
        created = domain_call(
            self._sessions.create_session,
            user_id=user_id,
            workspace_id=user_id,
        )
        return LoginResult(
            user_id,
            verified.username,
            created.session.session_id,
            created.session.workspace_id,
            created.cookie,
        )

    def current_user(self, session_handle: object):
        return self._identity.require_principal(session_handle)

    def logout(self, session_handle: object):
        if type(session_handle) is not str or len(session_handle) > 4096:
            raise ConversationError("UNAUTHENTICATED")
        domain_call(self._sessions.revoke_session, session_handle)
        return self._sessions.cookie_deletion()

    def _lifecycle(self, session_handle, operation, *args):
        principal = self.current_user(session_handle)
        result = self._repository.lifecycle(principal, operation, *args)
        if self.current_user(session_handle) != principal:
            raise ConversationError("IDENTITY_CHANGED")
        return result

    @staticmethod
    def _title(title):
        if type(title) is not str or not title.strip() or len(title) > 200:
            raise ConversationError("INVALID_ARGUMENT")
        return title.strip()

    def create_thread(self, session_handle: object, title: str = "New conversation"):
        return self._lifecycle(session_handle, "create_thread", self._title(title))

    def list_threads(self, session_handle: object):
        return self._lifecycle(session_handle, "list_threads")

    def rename_thread(self, session_handle: object, thread_id: str, title: str):
        _uuid(thread_id)
        return self._lifecycle(session_handle, "rename_thread", thread_id, self._title(title))

    def delete_thread(self, session_handle: object, thread_id: str):
        _uuid(thread_id)
        return self._lifecycle(session_handle, "delete_thread", thread_id)

    def open_thread(self, session_handle: object, thread_id: str):
        return self._conversation.open_thread(session_handle, thread_id)

    def handle_turn(
        self,
        session_handle: object,
        thread_id: str,
        text: str,
        request_id: str,
    ):
        return self._conversation.handle_turn(session_handle, thread_id, text, request_id)


def compose_account_backend(*, agent, connection_factory, account_secret: bytes) -> AccountBackend:
    """Requires actual P1/P2/P3 branches. Does not connect/run migrations on creation."""
    if type(account_secret) is not bytes or len(account_secret) < 32:
        raise ConversationError("CONFIGURATION_ERROR")
    try:
        from app.accounts.identity.postgres import PostgresAttemptRepository, PostgresUserRepository
        from app.accounts.identity.service import AuthService
        from app.accounts.identity.throttle import LoginThrottle
        from app.accounts.session.postgres import PostgresSessionRepository
        from app.accounts.session.service import SessionService
        from app.accounts.storage.authorization import PostgresAuthorizationRepository

        def key(label):
            return hmac.new(account_secret, label, hashlib.sha256).digest()

        authorization = PostgresAuthorizationRepository(connection_factory)
        auth = AuthService(
            PostgresUserRepository(connection_factory),
            LoginThrottle(PostgresAttemptRepository(connection_factory), pepper=key(b"login-v1")),
        )
        sessions = SessionService(
            PostgresSessionRepository(connection_factory),
            authorization,
            token_pepper=key(b"session-v1"),
        )
        identity = SessionIdentityAdapter(sessions, authorization)
        repository = PostgresConversationRepository(connection_factory)
        return AccountBackend(auth, sessions, identity, repository, agent)
    except Exception:
        raise ConversationError("CONFIGURATION_ERROR") from None


def build_account_backend(*, agent, secrets) -> AccountBackend:
    """P5 calls with secrets=st.secrets; DATABASE_URL never appears in returned errors."""
    try:
        database_url = secrets["DATABASE_URL"]
        secret = secrets["ACCOUNT_SECRET"]
        if type(database_url) is not str or not database_url.startswith(
            ("postgresql://", "postgres://"),
        ):
            raise ValueError()
        if type(secret) is not str or len(secret.encode("utf-8")) < 32:
            raise ValueError()
        from app.accounts.storage.database import create_connection_factory

        factory = create_connection_factory(database_url)
        return compose_account_backend(
            agent=agent,
            connection_factory=factory,
            account_secret=secret.encode("utf-8"),
        )
    except Exception:
        raise ConversationError("CONFIGURATION_ERROR") from None
