"""PostgreSQL persistence for user-owned chat conversations."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.accounts.storage.models import (
    AgentStateRecord,
    ChatMessage,
    ChatStorageError,
    ChatThread,
)


class PostgresChatRepository:
    """User-scoped PostgreSQL chat repository."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self.connection_factory = connection_factory

    @staticmethod
    def _uuid(value: str) -> str:
        try:
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError):
            raise ChatStorageError("INVALID_ARGUMENT") from None

    @staticmethod
    def _identity(user_id: str, workspace_id: str) -> None:
        if not all(
            isinstance(value, str) and value.strip() and value == value.strip()
            for value in (user_id, workspace_id)
        ):
            raise ChatStorageError("INVALID_ARGUMENT")

    def _read(self, query: str, params: tuple, *, many: bool = False):
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall() if many else cursor.fetchone()
            connection.commit()
            return rows
        except Exception:
            connection.rollback()
            raise ChatStorageError("STORAGE_ERROR") from None
        finally:
            connection.close()

    @staticmethod
    def _thread(row: tuple) -> ChatThread:
        return ChatThread(
            thread_id=str(row[0]),
            user_id=row[1],
            workspace_id=row[2],
            title=row[3],
            created_at=row[4],
            updated_at=row[5],
        )

    def create_thread(
        self, user_id: str, workspace_id: str, title: str
    ) -> ChatThread:
        self._identity(user_id, workspace_id)

        if not isinstance(title, str) or not title.strip():
            raise ChatStorageError("INVALID_ARGUMENT")

        thread_id = str(uuid4())
        now = datetime.now(timezone.utc)

        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO chat_threads (
                        thread_id, user_id, workspace_id,
                        title, created_at, updated_at
                    )
                    SELECT %s, user_id, workspace_id, %s, %s, %s
                    FROM account_workspaces
                    WHERE user_id = %s AND workspace_id = %s
                    RETURNING thread_id, user_id, workspace_id,
                              title, created_at, updated_at
                    """,
                    (
                        thread_id, title.strip(), now, now,
                        user_id, workspace_id,
                    ),
                )
                row = cursor.fetchone()

            if row is None:
                connection.rollback()
                raise ChatStorageError("NOT_FOUND")

            connection.commit()
            return self._thread(row)
        except ChatStorageError:
            raise
        except Exception:
            connection.rollback()
            raise ChatStorageError("STORAGE_ERROR") from None
        finally:
            connection.close()

    def list_threads(
        self, user_id: str, workspace_id: str
    ) -> list[ChatThread]:
        self._identity(user_id, workspace_id)

        rows = self._read(
            """
            SELECT thread_id, user_id, workspace_id,
                   title, created_at, updated_at
            FROM chat_threads
            WHERE user_id = %s AND workspace_id = %s
            ORDER BY updated_at DESC, thread_id DESC
            """,
            (user_id, workspace_id),
            many=True,
        )

        return [self._thread(row) for row in rows]

    def rename_thread(
        self,
        user_id: str,
        workspace_id: str,
        thread_id: str,
        title: str,
    ) -> ChatThread:
        self._identity(user_id, workspace_id)
        thread_id = self._uuid(thread_id)

        if not isinstance(title, str) or not title.strip():
            raise ChatStorageError("INVALID_ARGUMENT")

        row = self._read(
            """
            UPDATE chat_threads
            SET title = %s, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = %s AND workspace_id = %s
              AND thread_id = %s
            RETURNING thread_id, user_id, workspace_id,
                      title, created_at, updated_at
            """,
            (title.strip(), user_id, workspace_id, thread_id),
        )

        if row is None:
            raise ChatStorageError("NOT_FOUND")

        return self._thread(row)

    def delete_thread(
        self, user_id: str, workspace_id: str, thread_id: str
    ) -> None:
        self._identity(user_id, workspace_id)
        thread_id = self._uuid(thread_id)

        row = self._read(
            """
            DELETE FROM chat_threads
            WHERE user_id = %s AND workspace_id = %s
              AND thread_id = %s
            RETURNING thread_id
            """,
            (user_id, workspace_id, thread_id),
        )

        if row is None:
            raise ChatStorageError("NOT_FOUND")

    def list_messages(
        self, user_id: str, workspace_id: str, thread_id: str
    ) -> list[ChatMessage]:
        self._identity(user_id, workspace_id)
        thread_id = self._uuid(thread_id)

        rows = self._read(
            """
            SELECT m.message_id, m.thread_id, m.sequence,
                   m.role, m.content, m.request_id,
                   m.reply_json, m.created_at
            FROM chat_messages m
            JOIN chat_threads t ON t.thread_id = m.thread_id
            WHERE t.user_id = %s AND t.workspace_id = %s
              AND t.thread_id = %s
            ORDER BY m.sequence ASC
            """,
            (user_id, workspace_id, thread_id),
            many=True,
        )

        return [
            ChatMessage(
                message_id=str(row[0]),
                thread_id=str(row[1]),
                sequence=row[2],
                role=row[3],
                content=row[4],
                request_id=row[5],
                reply_json=row[6],
                created_at=row[7],
            )
            for row in rows
        ]

    def get_state(
        self, user_id: str, workspace_id: str, thread_id: str
    ) -> AgentStateRecord | None:
        self._identity(user_id, workspace_id)
        thread_id = self._uuid(thread_id)

        row = self._read(
            """
            SELECT s.state_id, s.thread_id, s.schema_version,
                   s.state_json, s.revision, s.updated_at
            FROM chat_agent_states s
            JOIN chat_threads t ON t.thread_id = s.thread_id
            WHERE t.user_id = %s AND t.workspace_id = %s
              AND t.thread_id = %s
            """,
            (user_id, workspace_id, thread_id),
        )

        if row is None:
            return None

        return AgentStateRecord(
            state_id=str(row[0]),
            thread_id=str(row[1]),
            schema_version=row[2],
            state_json=row[3],
            revision=row[4],
            updated_at=row[5],
        )

    def begin_turn(
        self,
        user_id: str,
        workspace_id: str,
        thread_id: str,
        request_id: str,
        user_message: str,
    ):
        """Atomically register a request and its user message."""
        import hashlib

        from app.accounts.storage.models import TurnRequest

        self._identity(user_id, workspace_id)
        thread_id = self._uuid(thread_id)

        if (
            not isinstance(request_id, str)
            or not request_id.strip()
            or request_id != request_id.strip()
            or not isinstance(user_message, str)
            or not user_message.strip()
        ):
            raise ChatStorageError("INVALID_ARGUMENT")

        fingerprint = hashlib.sha256(
            user_message.encode("utf-8")
        ).hexdigest()

        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                # Serialize turns for this thread, including sequence assignment.
                cursor.execute(
                    """
                    SELECT thread_id FROM chat_threads
                    WHERE thread_id = %s
                      AND user_id = %s AND workspace_id = %s
                    FOR UPDATE
                    """,
                    (thread_id, user_id, workspace_id),
                )
                if cursor.fetchone() is None:
                    raise ChatStorageError("NOT_FOUND")

                cursor.execute(
                    """
                    SELECT status, request_fingerprint,
                           created_at, updated_at
                    FROM chat_turn_requests
                    WHERE thread_id = %s AND request_id = %s
                    """,
                    (thread_id, request_id),
                )
                existing = cursor.fetchone()

                if existing is not None:
                    if existing[1] != fingerprint:
                        raise ChatStorageError("CONFLICT")
                    connection.commit()
                    return TurnRequest(
                        thread_id, request_id, existing[0],
                        existing[1], existing[2], existing[3],
                    )

                cursor.execute(
                    """
                    INSERT INTO chat_turn_requests (
                        thread_id, request_id, status,
                        request_fingerprint
                    )
                    VALUES (%s, %s, 'processing', %s)
                    RETURNING created_at, updated_at
                    """,
                    (thread_id, request_id, fingerprint),
                )
                created_at, updated_at = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM chat_messages WHERE thread_id = %s
                    """,
                    (thread_id,),
                )
                sequence = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO chat_messages (
                        message_id, thread_id, sequence,
                        role, content, request_id
                    )
                    VALUES (%s, %s, %s, 'user', %s, %s)
                    """,
                    (
                        str(uuid4()), thread_id, sequence,
                        user_message, request_id,
                    ),
                )

            connection.commit()
            return TurnRequest(
                thread_id, request_id, "processing",
                fingerprint, created_at, updated_at,
            )

        except ChatStorageError:
            connection.rollback()
            raise
        except Exception:
            connection.rollback()
            raise ChatStorageError("STORAGE_ERROR") from None
        finally:
            connection.close()

    def complete_turn(
        self,
        user_id: str,
        workspace_id: str,
        thread_id: str,
        request_id: str,
        assistant_message: str,
        reply_json: dict[str, Any],
        state_json: dict[str, Any],
        schema_version: int,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Atomically commit assistant reply and versioned Agent state."""
        import hashlib
        import json

        from psycopg.types.json import Jsonb

        self._identity(user_id, workspace_id)
        thread_id = self._uuid(thread_id)

        if (
            not isinstance(request_id, str)
            or not request_id.strip()
            or not isinstance(assistant_message, str)
            or not isinstance(reply_json, dict)
            or not isinstance(state_json, dict)
            or type(schema_version) is not int
            or schema_version < 1
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise ChatStorageError("INVALID_ARGUMENT")

        try:
            payload = json.dumps(
                {
                    "assistant_message": assistant_message,
                    "reply_json": reply_json,
                    "state_json": state_json,
                    "schema_version": schema_version,
                    "expected_revision": expected_revision,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ChatStorageError("INVALID_ARGUMENT") from None

        completion_fingerprint = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()

        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT thread_id FROM chat_threads
                    WHERE thread_id = %s
                      AND user_id = %s AND workspace_id = %s
                    FOR UPDATE
                    """,
                    (thread_id, user_id, workspace_id),
                )
                if cursor.fetchone() is None:
                    raise ChatStorageError("NOT_FOUND")

                cursor.execute(
                    """
                    SELECT status FROM chat_turn_requests
                    WHERE thread_id = %s AND request_id = %s
                    FOR UPDATE
                    """,
                    (thread_id, request_id),
                )
                request = cursor.fetchone()

                if request is None:
                    raise ChatStorageError("NOT_FOUND")

                if request[0] == "completed":
                    cursor.execute(
                        """
                        SELECT completion_fingerprint,
                               completed_revision,
                               assistant_message_id
                        FROM chat_turn_requests
                        WHERE thread_id = %s AND request_id = %s
                        """,
                        (thread_id, request_id),
                    )
                    receipt = cursor.fetchone()

                    if (
                        receipt is None
                        or receipt[0] is None
                        or receipt[0] != completion_fingerprint
                    ):
                        raise ChatStorageError("CONFLICT")

                    connection.commit()
                    return {
                        "ok": True,
                        "already_completed": True,
                        "message_id": str(receipt[2]),
                        "revision": receipt[1],
                    }

                if request[0] != "processing":
                    raise ChatStorageError("CONFLICT")

                cursor.execute(
                    """
                    SELECT revision FROM chat_agent_states
                    WHERE thread_id = %s FOR UPDATE
                    """,
                    (thread_id,),
                )
                state = cursor.fetchone()
                current_revision = state[0] if state else 0

                if current_revision != expected_revision:
                    raise ChatStorageError("CONFLICT")

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM chat_messages WHERE thread_id = %s
                    """,
                    (thread_id,),
                )
                sequence = cursor.fetchone()[0]

                message_id = str(uuid4())
                cursor.execute(
                    """
                    INSERT INTO chat_messages (
                        message_id, thread_id, sequence, role,
                        content, request_id, reply_json
                    )
                    VALUES (%s, %s, %s, 'assistant', %s, %s, %s)
                    """,
                    (
                        message_id, thread_id, sequence,
                        assistant_message, request_id,
                        Jsonb(reply_json),
                    ),
                )

                new_revision = current_revision + 1

                if state is None:
                    cursor.execute(
                        """
                        INSERT INTO chat_agent_states (
                            state_id, thread_id, schema_version,
                            state_json, revision
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()), thread_id, schema_version,
                            Jsonb(state_json), new_revision,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE chat_agent_states
                        SET schema_version = %s,
                            state_json = %s,
                            revision = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE thread_id = %s AND revision = %s
                        """,
                        (
                            schema_version, Jsonb(state_json),
                            new_revision, thread_id, current_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ChatStorageError("CONFLICT")

                cursor.execute(
                    """
                    UPDATE chat_turn_requests
                    SET status = 'completed',
                        completion_fingerprint = %s,
                        completed_revision = %s,
                        assistant_message_id = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE thread_id = %s AND request_id = %s
                    """,
                    (
                        completion_fingerprint,
                        new_revision,
                        message_id,
                        thread_id,
                        request_id,
                    ),
                )

                cursor.execute(
                    """
                    UPDATE chat_threads
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE thread_id = %s
                    """,
                    (thread_id,),
                )

            connection.commit()
            return {
                "ok": True,
                "already_completed": False,
                "message_id": message_id,
                "revision": new_revision,
            }

        except ChatStorageError:
            connection.rollback()
            raise
        except Exception:
            connection.rollback()
            raise ChatStorageError("STORAGE_ERROR") from None
        finally:
            connection.close()
