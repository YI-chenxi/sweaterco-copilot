"""One-use, same-origin HTTP handoffs. Session tokens remain on the server."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic

from app.accounts.session.models import SessionCookie

COOKIE_NAME = "sweaterco_session"


class TransportError(Exception):
    """A handoff expired, was replayed, or failed its browser-cookie check."""


@dataclass
class _Binding:
    handle: str | None = field(default=None, repr=False)
    pending: str | None = None
    ready: bool = False
    touched: float = 0


@dataclass(frozen=True)
class _Command:
    channel: str
    ticket: str
    expires: float
    cookie: SessionCookie | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Proof:
    command: _Command
    handle: str | None = field(repr=False)
    expires: float


class BrowserHandoffs:
    """Short-lived proofs bind browser requests to one server-side Streamlit session.

    The front end receives command/proof IDs, never the login bearer token.
    Cookie verification is a *second* HTTP request, after Set-Cookie is applied.
    Losing this process only loses handoffs; the next browser connection can
    restore its durable P2 session from the HttpOnly cookie.
    """

    def __init__(self, *, clock=monotonic, limit=4096):
        self._clock = clock
        self._limit = limit
        self._lock = RLock()
        self._bindings: dict[str, _Binding] = {}
        self._commands: dict[str, _Command] = {}
        self._verifications: dict[str, _Proof] = {}
        self._receipts: dict[str, _Proof] = {}

    def _prune(self):
        now = self._clock()
        for bucket in (self._commands, self._verifications, self._receipts):
            for key, item in list(bucket.items()):
                if item.expires <= now:
                    del bucket[key]
        for key, item in list(self._bindings.items()):
            if item.touched + 1800 <= now:
                del self._bindings[key]

    def new_channel(self):
        with self._lock:
            self._prune()
            if len(self._bindings) >= self._limit:
                raise TransportError("Browser connection limit reached")
            channel = secrets.token_urlsafe(32)
            self._bindings[channel] = _Binding(touched=self._clock())
            return channel

    def state(self, channel):
        with self._lock:
            self._prune()
            binding = self._bindings.get(channel)
            if binding is None:
                raise TransportError("Browser connection expired")
            binding.touched = self._clock()
            return binding.ready, binding.pending

    def read(self, channel):
        with self._lock:
            ready, _ = self.state(channel)
            return self._bindings[channel].handle if ready else None

    def issue(self, channel, cookie=None):
        if cookie is not None:
            if (
                not isinstance(cookie, SessionCookie)
                or cookie.name != COOKIE_NAME
                or cookie.path != "/"
                or cookie.http_only is not True
                or cookie.secure is not True
                or cookie.same_site != "Lax"
                or type(cookie.max_age) is not int
                or cookie.max_age < 0
                or type(cookie.value) is not str
                or (cookie.max_age > 0 and not 32 <= len(cookie.value) <= 4096)
                or (cookie.max_age == 0 and cookie.value != "")
            ):
                raise TransportError("Invalid server cookie instructions")
        with self._lock:
            self.state(channel)
            if sum(map(len, (self._commands, self._verifications, self._receipts))) >= self._limit:
                raise TransportError("Too many pending browser requests")
            ticket = secrets.token_urlsafe(32)
            binding = self._bindings[channel]
            binding.handle, binding.ready, binding.pending = None, False, ticket
            self._commands[ticket] = _Command(channel, ticket, self._clock() + 120, cookie)
            return ticket

    def _check(self, command):
        binding = self._bindings.get(command.channel)
        if command.expires <= self._clock() or binding is None or binding.pending != command.ticket:
            raise TransportError("Browser request expired")
        return binding

    def exchange(self, ticket, browser_handle):
        with self._lock:
            self._prune()
            command = self._commands.pop(ticket, None) if isinstance(ticket, str) else None
            if command is None:
                raise TransportError("Browser request expired")
            self._check(command)
            cookie = command.cookie
            expected = (
                browser_handle if cookie is None else (cookie.value if cookie.max_age else None)
            )
            if expected is not None and (type(expected) is not str or len(expected) > 4096):
                raise TransportError("Invalid browser cookie")
            verification = secrets.token_urlsafe(32)
            self._verifications[verification] = _Proof(command, expected, self._clock() + 120)
            return verification, cookie

    def verify(self, verification, browser_handle):
        with self._lock:
            self._prune()
            proof = self._verifications.pop(verification, None)
            if proof is None:
                raise TransportError("Browser verification expired")
            self._check(proof.command)
            expected = proof.handle
            if (expected is None and browser_handle is not None) or (
                expected is not None
                and (
                    type(browser_handle) is not str
                    or not hmac.compare_digest(expected.encode(), browser_handle.encode())
                )
            ):
                raise TransportError("The browser did not retain its login cookie")
            receipt = secrets.token_urlsafe(32)
            self._receipts[receipt] = _Proof(proof.command, expected, self._clock() + 120)
            return receipt

    def complete(self, channel, ticket, receipt):
        with self._lock:
            self._prune()
            proof = self._receipts.get(receipt) if isinstance(receipt, str) else None
            if proof is None or proof.command.channel != channel or proof.command.ticket != ticket:
                raise TransportError("Browser receipt does not match this connection")
            binding = self._check(proof.command)
            del self._receipts[receipt]
            binding.handle, binding.ready, binding.pending = proof.handle, True, None
            binding.touched = self._clock()

    def forget(self, channel):
        with self._lock:
            self._bindings.pop(channel, None)
