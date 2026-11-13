"""Pre-provisioned accounts with a login handle held by the Streamlit session."""

from copy import copy
from dataclasses import replace

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from app.ui.accounts.real_backend import RealAccountMode, initialize_real_account_mode

_MODE_KEY = "_streamlit_login_only_mode"
DEMO_LOGIN_USERNAME = "sweaterco_demo_guest"


class StreamlitSession:
    """Keep the opaque P2 handle on the server for this Streamlit session."""

    def __init__(self):
        self._handle: str | None = None

    def read(self):
        return self._handle

    def set_cookie(self, cookie):
        # P2 supplies its opaque handle in the cookie instruction. This adapter
        # holds it in Python state and never writes a browser cookie.
        self._handle = cookie.value

    def clear_cookie(self, _cookie):
        self._handle = None


class StreamlitTurnAgent:
    """Use the signed-in workspace for proactive commands in saved chat."""

    def __init__(self, agent_service, proactive_application):
        self._agent_service = agent_service
        self._proactive_application = proactive_application

    def handle_turn(self, text, session=None):
        service = copy(self._agent_service())
        service.proactive_application = self._proactive_application()
        return service.handle_turn(text, session)


def streamlit_login_mode(*, agent, chat_ready) -> RealAccountMode | None:
    """Opt in through app Secrets while retaining the original entrypoint."""
    try:
        enabled = st.secrets.get("ACCOUNT_LOGIN_ONLY", False)
    except StreamlitSecretNotFoundError:
        enabled = False

    if enabled is False:
        st.session_state.pop(_MODE_KEY, None)
        return None
    if enabled is not True:
        return RealAccountMode(backend=None, allow_registration=False)

    mode = st.session_state.get(_MODE_KEY)
    if mode is None:
        mode = initialize_real_account_mode(
            agent=agent,
            secrets=st.secrets,
            session=StreamlitSession(),
            reuse_connections=True,
        )
        demo_password = st.secrets.get("DEMO_LOGIN_PASSWORD")
        login_defaults = (
            (DEMO_LOGIN_USERNAME, demo_password)
            if isinstance(demo_password, str) and demo_password
            else None
        )
        mode = replace(mode, allow_registration=False, login_defaults=login_defaults)
        st.session_state[_MODE_KEY] = mode
    return replace(mode, chat_ready=mode.backend is not None and chat_ready())
