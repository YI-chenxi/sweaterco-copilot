"""Explicit server-side P4 initialization for the account workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.accounts.conversation import ConversationError, build_account_backend

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.accounts.conversation import AccountBackend, TurnAgent
    from app.ui.accounts.panels import BrowserSession


@dataclass(frozen=True)
class RealAccountMode:
    """Server-injected backend and optional, independently provided session transport."""

    backend: AccountBackend | None = field(repr=False)
    session: BrowserSession | None = field(default=None, repr=False)
    chat_ready: bool = field(default=False, compare=False)
    allow_registration: bool = True


def initialize_real_account_mode(
    *,
    agent: TurnAgent,
    secrets: Mapping[str, object],
    session: BrowserSession | None = None,
    reuse_connections: bool = False,
) -> RealAccountMode:
    """Compose P4 using the caller's settings without loading secrets or opening a DB.

    A configuration failure keeps real mode selected with no backend, so the
    main page can show a safe error and keep the private workspace closed.
    """
    try:
        backend = build_account_backend(
            agent=agent, secrets=secrets, reuse_connections=reuse_connections
        )
    except ConversationError:
        return RealAccountMode(backend=None)
    return RealAccountMode(backend, session)
