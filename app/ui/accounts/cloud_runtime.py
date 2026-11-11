"""One database-backed account runtime shared by native HTTP and Streamlit."""

from copy import copy
from dataclasses import dataclass
from threading import RLock

import streamlit as st

from app.accounts.conversation import build_account_backend
from app.accounts.session.browser_http import BrowserSettings
from app.accounts.session.browser_transport import BrowserHandoffs

_runtime = None
_lock = RLock()


class CloudTurnAgent:
    def handle_turn(self, text, session=None):
        from app.ui.streamlit_app import _proactive_application, agent_service

        service = copy(agent_service())
        service.proactive_application = _proactive_application()
        return service.handle_turn(text, session)


@dataclass(frozen=True)
class CloudRuntime:
    backend: object
    settings: BrowserSettings
    handoffs: BrowserHandoffs


def get_runtime():
    global _runtime
    with _lock:
        if _runtime is None:
            settings = BrowserSettings.parse(st.secrets["ACCOUNT_PUBLIC_URL"])
            backend = build_account_backend(agent=CloudTurnAgent(), secrets=st.secrets)
            _runtime = CloudRuntime(backend, settings, BrowserHandoffs())
        return _runtime
