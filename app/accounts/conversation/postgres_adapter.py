"""P4 transaction adapter over P3 0d2f90f; no schema/migration changes.

Short row locks serialize reservation and completion. No connection or transaction
is held while Agent executes. P3 still performs message/state writes.
"""

import hashlib
from contextlib import contextmanager
from uuid import UUID

from .codec import SCHEMA_VERSION, decode_reply, decode_session, document_copy
from .contracts import (
    ConversationSnapshot,
    PendingTurn,
    StoredMessage,
    TurnClaim,
    TurnReceipt,
)
from .errors import ConversationError


class _BorrowedConnection:
    """Give P3 our transaction; P4 alone commits, rolls back and closes it."""

    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class PostgresConversationRepository:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @contextmanager
    def _transaction(self, *, write=False):
        from app.accounts.storage.postgres import PostgresChatRepository

        connection = None
        try:
            connection = self.connection_factory()
            borrowed = _BorrowedConnection(connection)
            yield connection, PostgresChatRepository(lambda: borrowed)
            connection.commit()
        except Exception as error:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            if isinstance(error, ConversationError):
                raise
            code = getattr(error, "code", None)
            if code not in {"INVALID_ARGUMENT", "NOT_FOUND", "CONFLICT"}:
                code = "STORAGE_OUTCOME_UNKNOWN" if write else "STORAGE_ERROR"
            raise ConversationError(code) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    @staticmethod
    def _session(cursor, principal):
        # Resolve P2's trusted session again INSIDE the short storage transaction.
        # Lock order is session/workspace -> thread; logout cannot race this write.
        cursor.execute(
            """SELECT s.session_id
               FROM account_sessions s
               JOIN account_workspaces w
                 ON w.workspace_id = s.workspace_id AND w.user_id = s.user_id
               WHERE s.session_id = %s AND s.user_id = %s AND s.workspace_id = %s
                 AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp()
               FOR SHARE OF s, w""",
            (principal.session_id, principal.user_id, principal.workspace_id),
        )
        if cursor.fetchone() is None:
            raise ConversationError("UNAUTHENTICATED")

    @staticmethod
    def _thread(cursor, principal, thread_id, *, exclusive):
        lock = "FOR UPDATE" if exclusive else "FOR SHARE"
        cursor.execute(
            """SELECT thread_id FROM chat_threads
               WHERE thread_id = %s AND user_id = %s AND workspace_id = %s """
            + lock,
            (thread_id, principal.user_id, principal.workspace_id),
        )
        if cursor.fetchone() is None:
            # Same result for missing/deleted/foreign threads; do not expose ownership.
            raise ConversationError("NOT_FOUND")

    @staticmethod
    def _request(cursor, thread_id, request_id):
        cursor.execute(
            """SELECT status, request_fingerprint, completion_fingerprint,
                      completed_revision, assistant_message_id
               FROM chat_turn_requests WHERE thread_id = %s AND request_id = %s""",
            (thread_id, request_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _pending(cursor, thread_id):
        cursor.execute(
            """SELECT request_id, status FROM chat_turn_requests
               WHERE thread_id = %s AND status <> 'completed'
               ORDER BY created_at, request_id""",
            (thread_id,),
        )
        rows = cursor.fetchall()
        if len(rows) > 1:
            raise ConversationError("RECONCILIATION_REQUIRED")
        if rows and rows[0][1] not in ("processing", "failed"):
            raise ConversationError("INVALID_STATE")
        return PendingTurn(*rows[0]) if rows else None

    def load(self, principal, thread_id):
        with self._transaction() as (connection, repository):
            with connection.cursor() as cursor:
                self._session(cursor, principal)
                self._thread(cursor, principal, thread_id, exclusive=False)
                pending = self._pending(cursor, thread_id)
            # P3 writers lock this thread FOR UPDATE, so these two reads are coherent.
            messages = repository.list_messages(
                principal.user_id,
                principal.workspace_id,
                thread_id,
            )
            state = repository.get_state(principal.user_id, principal.workspace_id, thread_id)
            if state is not None and type(state.state_json) is not dict:
                raise ConversationError("INVALID_STATE")
            if state is not None and (
                state.schema_version != SCHEMA_VERSION
                or state.state_json.get("schema_version") != state.schema_version
            ):
                raise ConversationError("UNSUPPORTED_SCHEMA")
            return ConversationSnapshot(
                thread_id,
                principal.user_id,
                principal.workspace_id,
                state.revision if state else 0,
                tuple(
                    StoredMessage(
                        m.message_id,
                        m.request_id,
                        m.sequence,
                        m.role,
                        m.content,
                        document_copy(m.reply_json) if m.reply_json is not None else None,
                    )
                    for m in messages
                ),
                document_copy(state.state_json) if state else None,
                pending,
            )

    @staticmethod
    def _replay(cursor, principal, thread_id, request_id, text, request):
        _, _, fingerprint, revision, assistant_id = request
        if (
            type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
            or type(revision) is not int
            or revision < 1
            or assistant_id is None
        ):
            # 002 migration deliberately does not invent receipts for legacy requests.
            raise ConversationError("RECONCILIATION_REQUIRED")
        cursor.execute(
            """SELECT role, content, reply_json, message_id
               FROM chat_messages WHERE thread_id = %s AND request_id = %s
               ORDER BY sequence""",
            (thread_id, request_id),
        )
        messages = cursor.fetchall()
        if (
            len(messages) != 2
            or messages[0][0] != "user"
            or messages[0][1] != text
            or messages[1][0] != "assistant"
            or str(messages[1][3]) != str(assistant_id)
        ):
            raise ConversationError("INVALID_STATE")
        reply = document_copy(messages[1][2])
        if decode_reply(reply).text != messages[1][1]:
            raise ConversationError("INVALID_STATE")
        # P3 retains current state only. Replays return old reply, never old writable state.
        return TurnReceipt(
            thread_id,
            principal.user_id,
            principal.workspace_id,
            request_id,
            text,
            revision - 1,
            revision,
            reply,
            None,
        )

    def begin_turn(self, principal, thread_id, request_id, user_text, expected_revision):
        fingerprint = hashlib.sha256(user_text.encode("utf-8")).hexdigest()
        with self._transaction(write=True) as (connection, repository):
            with connection.cursor() as cursor:
                self._session(cursor, principal)
                self._thread(cursor, principal, thread_id, exclusive=True)
                request = self._request(cursor, thread_id, request_id)
                if request is not None:
                    if request[1] != fingerprint:
                        raise ConversationError("CONFLICT")
                    if request[0] == "completed":
                        return TurnClaim(
                            "completed",
                            self._replay(
                                cursor,
                                principal,
                                thread_id,
                                request_id,
                                user_text,
                                request,
                            ),
                        )
                    if request[0] == "processing":
                        return TurnClaim("in_progress")
                    if request[0] == "failed":
                        return TurnClaim("uncertain")
                    raise ConversationError("INVALID_STATE")
                if self._pending(cursor, thread_id) is not None:
                    raise ConversationError("REQUEST_IN_PROGRESS")
            current = repository.get_state(principal.user_id, principal.workspace_id, thread_id)
            if (current.revision if current else 0) != expected_revision:
                raise ConversationError("CONFLICT")
            reserved = repository.begin_turn(
                principal.user_id,
                principal.workspace_id,
                thread_id,
                request_id,
                user_text,
            )
            if reserved.status != "processing" or reserved.request_fingerprint != fingerprint:
                raise ConversationError("INVALID_STATE")
            # Return leaves context manager: the outer transaction commits BEFORE acquired.
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
        reply_document, state_document = (
            document_copy(reply_document),
            document_copy(state_document),
        )
        reply = decode_reply(reply_document)
        decode_session(state_document)
        with self._transaction(write=True) as (connection, repository):
            with connection.cursor() as cursor:
                self._session(cursor, principal)
                self._thread(cursor, principal, thread_id, exclusive=True)
                request = self._request(cursor, thread_id, request_id)
                if request is None:
                    raise ConversationError("NOT_FOUND")
                cursor.execute(
                    """SELECT content FROM chat_messages
                       WHERE thread_id = %s AND request_id = %s AND role = 'user'""",
                    (thread_id, request_id),
                )
                user = cursor.fetchone()
                if user is None:
                    raise ConversationError("INVALID_STATE")
            result = repository.complete_turn(
                principal.user_id,
                principal.workspace_id,
                thread_id,
                request_id,
                reply.text,
                reply_document,
                state_document,
                SCHEMA_VERSION,
                expected_revision,
            )
            if result.get("ok") is not True or result.get("revision") != expected_revision + 1:
                raise ConversationError("INVALID_STATE")
            UUID(result["message_id"])
            return TurnReceipt(
                thread_id,
                principal.user_id,
                principal.workspace_id,
                request_id,
                user[0],
                expected_revision,
                result["revision"],
                reply_document,
                state_document,
            )

    def mark_uncertain(self, principal, thread_id, request_id, error_code):
        # Internal cleanup after a previously verified request. May run AFTER logout.
        # Do not require a live session here; retain scoped ownership and reservation.
        with self._transaction(write=True) as (connection, _):
            with connection.cursor() as cursor:
                self._thread(cursor, principal, thread_id, exclusive=True)
                cursor.execute(
                    """UPDATE chat_turn_requests SET status = 'failed',
                              updated_at = CURRENT_TIMESTAMP
                       WHERE thread_id = %s AND request_id = %s AND status = 'processing'""",
                    (thread_id, request_id),
                )

    def lifecycle(self, principal, operation, *args):
        """Only the account facade uses this; no frontend-supplied identities."""
        if operation not in {"create_thread", "list_threads", "rename_thread", "delete_thread"}:
            raise ConversationError("INVALID_ARGUMENT")
        with self._transaction(write=operation != "list_threads") as (connection, repository):
            with connection.cursor() as cursor:
                self._session(cursor, principal)
                if operation in {"rename_thread", "delete_thread"}:
                    self._thread(cursor, principal, args[0], exclusive=True)
            return getattr(repository, operation)(principal.user_id, principal.workspace_id, *args)
