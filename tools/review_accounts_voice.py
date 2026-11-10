"""Loopback-only UI review harness. Not a deployment entry point or durable account store."""

import os
import sys
from copy import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

if os.getenv("SWEATERCO_LOCAL_REVIEW") != "1":
    raise RuntimeError("Start this explicit local review harness with SWEATERCO_LOCAL_REVIEW=1")
if st.get_option("server.address") not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("This review harness must only listen on loopback")

from app.accounts.conversation.identity_adapter import SessionIdentityAdapter  # noqa: E402
from app.accounts.conversation.runtime import AccountBackend  # noqa: E402
from app.accounts.identity.service import AuthService  # noqa: E402
from app.accounts.identity.throttle import InMemoryAttemptRepository, LoginThrottle  # noqa: E402
from app.accounts.session.authorization import InMemoryAuthorizationRepository  # noqa: E402
from app.accounts.session.repository import InMemorySessionRepository  # noqa: E402
from app.accounts.session.service import SessionService  # noqa: E402
from app.ui.accounts.workspace import AccountTestMode  # noqa: E402
from app.ui.streamlit_app import _chat_ready, _proactive_application, agent_service, main  # noqa: E402
from tests.accounts.ui.test_account_chat_integration import FakeBrowser  # noqa: E402
from tests.accounts.ui.test_real_account_mode import (  # noqa: E402
    PrivateWorkspaceUsers,
    ThreadRepository,
)


class ReviewThreads(ThreadRepository):
    def lifecycle(self, principal, operation, *args):
        if operation not in {"rename_thread", "delete_thread"}:
            return super().lifecycle(principal, operation, *args)
        with self.lock:
            self.load(principal, args[0])
            if operation == "rename_thread":
                self.titles[args[0]] = args[1]
                return None
            del self.threads[args[0]]
            del self.titles[args[0]]


class ReviewAgent:
    def handle_turn(self, text, session=None):
        service = copy(agent_service())
        service.proactive_application = _proactive_application()
        return service.handle_turn(text, session)


@st.cache_resource
def review_backend():
    import secrets

    authorization = InMemoryAuthorizationRepository()
    auth = AuthService(
        PrivateWorkspaceUsers(authorization),
        LoginThrottle(InMemoryAttemptRepository(), pepper=secrets.token_bytes(32)),
    )
    sessions = SessionService(InMemorySessionRepository(), authorization)
    backend = AccountBackend(
        auth,
        sessions,
        SessionIdentityAdapter(sessions, authorization),
        ReviewThreads(),
        ReviewAgent(),
    )
    for name in ("preview_alice", "preview_bob"):
        backend.register(name, "SweaterCo preview account 2026!")
    return backend


transport = st.session_state.setdefault("local_review_transport", FakeBrowser())
main(AccountTestMode(review_backend(), transport, chat_ready=_chat_ready()))
