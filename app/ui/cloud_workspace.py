"""Private cloud workspace. Configuration/transport failures never open anonymous data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app.accounts.session.browser_transport import TransportError  # noqa: E402
from app.ui.accounts.browser_session import HttpBrowserSession  # noqa: E402
from app.ui.accounts.cloud_runtime import get_runtime  # noqa: E402
from app.ui.accounts.real_backend import RealAccountMode  # noqa: E402
from app.ui.streamlit_app import _chat_ready, _configure_page, main  # noqa: E402

try:
    runtime = get_runtime()
except Exception:
    main(real_account_mode=RealAccountMode(None))
else:
    _configure_page()
    transport = st.session_state.get("cloud_browser_session")
    if transport is None:
        try:
            transport = HttpBrowserSession(runtime.handoffs)
        except TransportError:
            main(real_account_mode=RealAccountMode(None))
            st.stop()
        st.session_state["cloud_browser_session"] = transport
    if transport.render():
        main(
            real_account_mode=RealAccountMode(runtime.backend, transport, chat_ready=_chat_ready())
        )
