"""Test-only adapters. In-memory, NOT a production login or durable database."""

from copy import deepcopy
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from app.accounts.conversation.codec import decode_reply
from app.accounts.conversation.contracts import (
    ConversationSnapshot,
    Principal,
    StoredMessage,
    TurnClaim,
    TurnReceipt,
)
from app.accounts.conversation.errors import ConversationError
from app.agent.contracts import AgentReply, Evidence
from app.agent.turn_service import TurnResult


def uid():
    return str(uuid4())


class FakeIdentity:
    def __init__(self):
        self.users = {
            "a": Principal("alice", "factory", "session-a"),
            "b": Principal("bob", "factory", "session-b"),
            "other": Principal("alice", "other-factory", "session-c"),
        }
        self.calls = 0
        self.hook = None

    def require_principal(self, handle):
        self.calls += 1
        if self.hook:
            self.hook(self.calls)
        if handle not in self.users:
            raise ConversationError("UNAUTHENTICATED")
        return self.users[handle]


class FakeRepository:
    """Imitates required atomic semantics under a lock; does not simulate disk survival."""

    def __init__(self):
        self.lock = RLock()
        self.threads = {}
        self.requests = {}
        self.fail = None

    def create(self, principal):
        thread = uid()
        self.threads[thread] = ConversationSnapshot(
            thread,
            principal.user_id,
            principal.workspace_id,
            0,
        )
        return thread

    def _check(self, principal, thread):
        if thread not in self.threads:
            raise ConversationError("NOT_FOUND")
        snapshot = self.threads[thread]
        if (snapshot.owner_user_id, snapshot.workspace_id) != (
            principal.user_id,
            principal.workspace_id,
        ):
            raise ConversationError("FORBIDDEN")
        return snapshot

    def _fail(self, point):
        if self.fail == point:
            self.fail = None
            raise RuntimeError("private database error with SECRET")

    def load(self, principal, thread_id):
        with self.lock:
            self._fail("load")
            return deepcopy(self._check(principal, thread_id))

    def begin_turn(self, principal, thread_id, request_id, user_text, expected_revision):
        with self.lock:
            current = self._check(principal, thread_id)
            key = (thread_id, request_id)
            if key in self.requests:
                saved = self.requests[key]
                if saved["text"] != user_text:
                    raise ConversationError("CONFLICT")
                return TurnClaim(saved["status"], deepcopy(saved.get("receipt")))
            if any(
                t == thread_id and item["status"] in ("in_progress", "uncertain")
                for (t, _), item in self.requests.items()
            ):
                raise ConversationError("REQUEST_IN_PROGRESS")
            if current.revision != expected_revision:
                raise ConversationError("CONFLICT")
            self._fail("before_claim")
            self.requests[key] = {
                "text": user_text,
                "status": "in_progress",
                "revision": expected_revision,
            }
            self._fail("after_claim")
            return TurnClaim("acquired")

    def commit_turn(
        self,
        principal,
        thread_id,
        request_id,
        *,
        expected_revision,
        reply_document,
        state_document,
    ):
        with self.lock:
            current = self._check(principal, thread_id)
            key = (thread_id, request_id)
            claim = self.requests[key]
            if claim["status"] == "completed":
                return deepcopy(claim["receipt"])
            if (
                claim["status"] != "in_progress"
                or current.revision != expected_revision
                or claim["revision"] != expected_revision
            ):
                raise ConversationError("CONFLICT")
            self._fail("before_commit")
            receipt = TurnReceipt(
                thread_id,
                principal.user_id,
                principal.workspace_id,
                request_id,
                claim["text"],
                expected_revision,
                expected_revision + 1,
                deepcopy(reply_document),
                deepcopy(state_document),
            )
            count = len(current.messages)
            messages = (
                StoredMessage(uid(), request_id, count + 1, "user", claim["text"]),
                StoredMessage(
                    uid(),
                    request_id,
                    count + 2,
                    "assistant",
                    decode_reply(reply_document).text,
                    deepcopy(reply_document),
                ),
            )
            self.threads[thread_id] = replace(
                current,
                revision=receipt.revision,
                messages=current.messages + messages,
                state_document=deepcopy(state_document),
            )
            claim["status"] = "completed"
            claim["receipt"] = receipt
            self._fail("after_commit")
            return deepcopy(receipt)

    def mark_uncertain(self, principal, thread_id, request_id, error_code):
        with self.lock:
            self._fail("mark_uncertain")
            self._check(principal, thread_id)
            claim = self.requests[(thread_id, request_id)]
            if claim["status"] != "completed":
                claim.update(status="uncertain", error_code=error_code)


class FakeAgent:
    def __init__(self):
        self.calls = 0
        self.hook = None

    def handle_turn(self, text, session):
        self.calls += 1
        if self.hook:
            self.hook(session)
        session.next_task_number += 1
        return TurnResult(
            AgentReply(
                text="Result: " + text,
                evidence=[Evidence("orders.csv", "ORD-057", "Matched row")],
                warnings=["Check dates"],
                assumptions=["Sunday excluded"],
                metadata={"source_rows": [{"order_id": "ORD-057"}]},
            ),
            session,
        )
