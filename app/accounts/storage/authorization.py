"""PostgreSQL authorization queries for P2 session services."""

from collections.abc import Callable
from typing import Any
from uuid import UUID


class PostgresAuthorizationRepository:
    """Read server-owned workspace and chat resource relationships."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self.connection_factory = connection_factory

    def _fetch_one(self, query: str, parameters: tuple) -> tuple | None:
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                row = cursor.fetchone()
            connection.commit()
            return row
        except Exception:
            connection.rollback()
            # Never return False/None on database failure:
            # callers must distinguish storage failure from denied access.
            raise
        finally:
            connection.close()

    @staticmethod
    def _uuid(value: str) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return str(UUID(value))
        except ValueError:
            return None

    def can_access_workspace(
        self, user_id: str, workspace_id: str
    ) -> bool:
        if not isinstance(user_id, str) or not isinstance(workspace_id, str):
            return False
        if not user_id.strip() or not workspace_id.strip():
            return False

        row = self._fetch_one(
            """
            SELECT 1 FROM account_workspaces
            WHERE user_id = %s AND workspace_id = %s
            """,
            (user_id, workspace_id),
        )
        return row is not None

    def get_thread_owner(self, thread_id: str) -> tuple[str, str] | None:
        thread_id = self._uuid(thread_id)
        if thread_id is None:
            return None

        row = self._fetch_one(
            """
            SELECT user_id, workspace_id
            FROM chat_threads WHERE thread_id = %s
            """,
            (thread_id,),
        )
        return (str(row[0]), str(row[1])) if row else None

    def get_message_thread_id(self, message_id: str) -> str | None:
        message_id = self._uuid(message_id)
        if message_id is None:
            return None

        row = self._fetch_one(
            """
            SELECT thread_id
            FROM chat_messages WHERE message_id = %s
            """,
            (message_id,),
        )
        return str(row[0]) if row else None

    def get_state_thread_id(self, state_id: str) -> str | None:
        state_id = self._uuid(state_id)
        if state_id is None:
            return None

        row = self._fetch_one(
            """
            SELECT thread_id
            FROM chat_agent_states WHERE state_id = %s
            """,
            (state_id,),
        )
        return str(row[0]) if row else None
