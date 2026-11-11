"""Exercise real HTTP cookie delivery without exposing or accepting client identity IDs."""

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.accounts.session.authorization import InMemoryAuthorizationRepository
from app.accounts.session.browser_http import ENDPOINT, BrowserSettings, browser_routes
from app.accounts.session.browser_transport import COOKIE_NAME, BrowserHandoffs, TransportError
from app.accounts.session.models import InvalidSession
from app.accounts.session.repository import InMemorySessionRepository
from app.accounts.session.service import SessionService

ORIGIN = "https://account-test.example"
ROUTE = "/" + ENDPOINT


@pytest.fixture
def setup():
    authorization = InMemoryAuthorizationRepository()
    for person in ("alice", "bob"):
        authorization.grant_workspace(person, person)
    sessions = SessionService(InMemorySessionRepository(), authorization)
    handoffs = BrowserHandoffs()
    runtime = SimpleNamespace(settings=BrowserSettings.parse(ORIGIN), handoffs=handoffs)
    app = Starlette(routes=browser_routes(lambda: runtime))
    return sessions, handoffs, app


def client(app):
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def exchange(client, ticket):
    response = client.post(ROUTE, json={"command": ticket})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return response


def verify(client, response):
    checked = client.post(ROUTE, json=response.json())
    assert checked.status_code == 200
    assert set(checked.json()) == {"receipt"}
    return checked.json()["receipt"]


def test_login_refresh_and_logout_cookie_round_trip(setup):
    sessions, handoffs, app = setup
    with client(app) as browser:
        original = handoffs.new_channel()
        login = sessions.create_session(user_id="alice", workspace_id="alice")
        ticket = handoffs.issue(original, login.cookie)
        assert handoffs.read(original) is None
        response = exchange(browser, ticket)
        header = response.headers["set-cookie"].lower()
        assert all(attr in header for attr in ("httponly", "secure", "samesite=lax", "path=/"))
        assert login.token not in response.text
        receipt = verify(browser, response)
        assert login.token not in receipt
        assert handoffs.read(original) is None
        handoffs.complete(original, ticket, receipt)
        assert sessions.resolve_current_user(handoffs.read(original)).user_id == "alice"

        # Refresh makes a new Streamlit connection. Recovery comes from HTTP,
        # not the previous Streamlit browser transport object.
        refreshed = handoffs.new_channel()
        probe = handoffs.issue(refreshed)
        handoffs.complete(refreshed, probe, verify(browser, exchange(browser, probe)))
        assert handoffs.read(refreshed) == login.token

        sessions.revoke_session(handoffs.read(refreshed))
        logout = handoffs.issue(refreshed, sessions.cookie_deletion())
        cleared = exchange(browser, logout)
        assert "max-age=0" in cleared.headers["set-cookie"].lower()
        handoffs.complete(refreshed, logout, verify(browser, cleared))
        assert COOKIE_NAME not in browser.cookies
        assert handoffs.read(refreshed) is None
        with pytest.raises(InvalidSession):
            sessions.resolve_session(handoffs.read(original))
        fresh = handoffs.new_channel()
        probe = handoffs.issue(fresh)
        handoffs.complete(fresh, probe, verify(browser, exchange(browser, probe)))
        assert handoffs.read(fresh) is None


def test_receipts_cannot_cross_connections_or_replay(setup):
    sessions, handoffs, app = setup
    with client(app) as alice, client(app) as bob:
        a, b = handoffs.new_channel(), handoffs.new_channel()
        a_login = sessions.create_session(user_id="alice", workspace_id="alice")
        b_login = sessions.create_session(user_id="bob", workspace_id="bob")
        a_ticket = handoffs.issue(a, a_login.cookie)
        b_ticket = handoffs.issue(b, b_login.cookie)
        a_receipt = verify(alice, exchange(alice, a_ticket))
        b_receipt = verify(bob, exchange(bob, b_ticket))
        with pytest.raises(TransportError):
            handoffs.complete(b, b_ticket, a_receipt)
        handoffs.complete(a, a_ticket, a_receipt)
        handoffs.complete(b, b_ticket, b_receipt)
        assert sessions.resolve_current_user(handoffs.read(a)).user_id == "alice"
        assert sessions.resolve_current_user(handoffs.read(b)).user_id == "bob"
        assert alice.post(ROUTE, json={"command": a_ticket}).status_code == 400
        with pytest.raises(TransportError):
            handoffs.complete(a, a_ticket, a_receipt)


def test_cookie_not_forwarded_cannot_establish_login(setup):
    sessions, handoffs, app = setup
    with client(app) as browser:
        channel = handoffs.new_channel()
        login = sessions.create_session(user_id="alice", workspace_id="alice")
        ticket = handoffs.issue(channel, login.cookie)
        response = exchange(browser, ticket)
        browser.cookies.clear()
        assert browser.post(ROUTE, json=response.json()).status_code == 400
        assert handoffs.read(channel) is None
        assert handoffs.state(channel)[0] is False


def test_cross_origin_and_malformed_requests_are_rejected_before_exchange(setup):
    _, handoffs, app = setup
    with client(app) as browser:
        channel = handoffs.new_channel()
        ticket = handoffs.issue(channel)
        assert browser.get(ROUTE).status_code == 405
        assert (
            browser.post(
                ROUTE, json={"command": ticket}, headers={"Origin": "https://another.example"}
            ).status_code
            == 403
        )
        assert (
            browser.post(
                ROUTE, json={"command": ticket}, headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
        assert browser.post(ROUTE, data={"command": ticket}).status_code == 403
        assert browser.post(ROUTE, json={"command": ticket, "user_id": "alice"}).status_code == 400
        assert browser.post(ROUTE, json={"command": "x" * 2048}).status_code == 400
        handoffs.complete(channel, ticket, verify(browser, exchange(browser, ticket)))
        assert handoffs.read(channel) is None


def test_expired_and_superseded_handoffs_do_not_authenticate():
    now = [100.0]
    handoffs = BrowserHandoffs(clock=lambda: now[0])
    channel = handoffs.new_channel()
    first = handoffs.issue(channel)
    second = handoffs.issue(channel)
    with pytest.raises(TransportError):
        handoffs.exchange(first, None)
    verification, _ = handoffs.exchange(second, None)
    now[0] += 121
    with pytest.raises(TransportError):
        handoffs.verify(verification, None)
    assert handoffs.read(channel) is None


def test_reconnected_streamlit_client_must_prove_its_cookie_again(setup, monkeypatch):
    from app.ui.accounts import browser_session

    sessions, handoffs, app = setup
    current_socket = [b"original connection"]
    monkeypatch.setattr(browser_session, "_connection", lambda: current_socket[0])
    adapter = browser_session.HttpBrowserSession(handoffs)
    with client(app) as browser:
        initial = adapter._ticket
        adapter.complete(
            {"command": initial, "receipt": verify(browser, exchange(browser, initial))}
        )
        login = sessions.create_session(user_id="alice", workspace_id="alice")
        adapter.set_cookie(login.cookie)
        ticket = adapter._ticket
        receipt = verify(browser, exchange(browser, ticket))
        adapter.complete({"command": ticket, "receipt": receipt})
        assert adapter.read() == login.token

        current_socket[0] = b"different connection with retained Python state"
        assert adapter.read() is None
        with pytest.raises(TransportError):
            adapter.set_cookie(login.cookie)
        adapter.retry()
        adapter.complete({"command": ticket, "receipt": receipt})
        assert adapter.read() is None
        probe = adapter._ticket
        adapter.complete({"command": probe, "receipt": verify(browser, exchange(browser, probe))})
        assert adapter.read() == login.token


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://account-test.example/path",
        "https://user@account-test.example",
        "https://account-test.example?q=wrong",
        None,
    ],
)
def test_public_origin_must_be_exact_https_origin(url):
    with pytest.raises(ValueError):
        BrowserSettings.parse(url)
