"""BrowserSession implementation; only the HTTP layer writes login cookies."""

from hashlib import sha256
from pathlib import Path

import streamlit as st
from streamlit.components.v2 import component

from app.accounts.session.browser_http import ENDPOINT
from app.accounts.session.browser_transport import TransportError
from app.ui.language import shared_language


@st.cache_resource(show_spinner=False)
def _bridge(js):
    return component("sweaterco_secure_login_" + sha256(js.encode()).hexdigest()[:8], js=js)


def _connection():
    # Streamlit can retain Python state when a WebSocket reconnects. Tie a
    # successful HTTP handoff to the current connection, not that retained state.
    value = st.context.headers.get("Sec-WebSocket-Key")
    return sha256(value.encode()).digest() if value else None


class HttpBrowserSession:
    def __init__(self, handoffs):
        self._handoffs = handoffs
        self._channel = None
        self.retry()

    def read(self):
        if not self._same_connection():
            return None
        try:
            return self._handoffs.read(self._channel)
        except TransportError:
            return None

    def set_cookie(self, cookie):
        if not self._same_connection():
            raise TransportError("Browser connection changed")
        self._ticket = self._handoffs.issue(self._channel, cookie)
        self.failed = False

    def clear_cookie(self, cookie):
        self.set_cookie(cookie)

    def retry(self):
        connection = _connection()
        if connection is None:
            raise TransportError("Browser connection unavailable")
        if self._channel is not None:
            self._handoffs.forget(self._channel)
        self._connection = connection
        self._channel = self._handoffs.new_channel()
        self._ticket = self._handoffs.issue(self._channel)
        self.failed = False

    def complete(self, result):
        if (
            not self._same_connection()
            or not isinstance(result, dict)
            or result.get("command") != self._ticket
        ):
            return
        try:
            if result.get("error"):
                raise TransportError()
            self._handoffs.complete(self._channel, self._ticket, result.get("receipt"))
        except TransportError:
            self.failed = True

    def _same_connection(self):
        return self._connection == _connection()

    def render(self):
        try:
            if not self._same_connection():
                self.retry()
            ready, _ = self._handoffs.state(self._channel)
        except TransportError:
            try:
                self.retry()
                ready = False
            except TransportError:
                self.failed = True
                ready = False
        if ready:
            return True
        zh = shared_language(st.session_state) == "zh"
        if self.failed:
            st.error(
                "无法建立浏览器登录状态。请重新连接；若持续出现，请联系部署负责人。"
                if zh
                else "Could not establish browser sign-in. Reconnect or contact the app maintainer."
            )
            if st.button("重新连接" if zh else "Reconnect", key="cloud_session_reconnect"):
                try:
                    self.retry()
                except TransportError:
                    pass
                else:
                    st.rerun()
            return False
        key = "cloud_cookie_" + self._ticket

        def received():
            self.complete(st.session_state.get(key, {}).get("result"))

        _bridge(Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))(
            key=key,
            height=0,
            data={"command": self._ticket, "endpoint": ENDPOINT},
            on_result_change=received,
        )
        st.caption("正在建立安全连接…" if zh else "Establishing a secure connection…")
        return False
