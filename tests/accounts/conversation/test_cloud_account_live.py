"""Opt-in: HTTP cookies and persisted accounts together in an isolated test schema."""

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from app.accounts.conversation import ConversationError
from app.accounts.session.browser_http import BrowserSettings, browser_routes
from app.accounts.session.browser_transport import BrowserHandoffs
from tests.accounts.conversation.fakes import uid
from tests.accounts.conversation.test_postgres_live import create_user, make_backend
from tests.accounts.session.test_browser_transport import ORIGIN, client, exchange, verify

pytest_plugins = ["tests.accounts.conversation.test_postgres_live"]


def test_http_login_and_postgres_history_survive_fresh_backend(real_factory):
    backend = make_backend(real_factory)
    runtime = SimpleNamespace(settings=BrowserSettings.parse(ORIGIN), handoffs=BrowserHandoffs())
    app = Starlette(routes=browser_routes(lambda: runtime))

    def connect(browser, cookie=None):
        handoffs = runtime.handoffs
        connection = handoffs.new_channel()
        command = handoffs.issue(connection, cookie)
        handoffs.complete(connection, command, verify(browser, exchange(browser, command)))
        return handoffs.read(connection)

    with client(app) as alice, client(app) as bob:
        login_a, login_b = create_user(backend), create_user(backend)
        handle = connect(alice, login_a.cookie)
        thread = backend.create_thread(handle, "Private cloud test")
        backend.handle_turn(handle, thread.thread_id, "Saved account test message", uid())

        # Fresh P4 services, database connections and transport memory, as after reboot.
        backend = make_backend(real_factory)
        runtime.handoffs = BrowserHandoffs()
        restored = connect(alice)
        history = backend.open_thread(restored, thread.thread_id)
        assert history.messages[0].content == "Saved account test message"
        assert history.messages[1].reply.evidence
        assert history.session.next_task_number == 2
        other = connect(bob, login_b.cookie)
        assert not backend.list_threads(other)
        with pytest.raises(ConversationError) as error:
            backend.open_thread(other, thread.thread_id)
        assert error.value.code in {"FORBIDDEN", "NOT_FOUND"}

        assert connect(alice, backend.logout(restored)) is None
        assert connect(alice) is None
        with pytest.raises(ConversationError) as error:
            backend.current_user(restored)
        assert error.value.code == "UNAUTHENTICATED"
