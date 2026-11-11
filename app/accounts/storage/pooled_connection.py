"""Connection lease compatible with repositories that call close()."""

from threading import Lock


class PooledConnection:
    """Return a borrowed psycopg connection to its pool exactly once."""

    def __init__(self, pool, connection):
        self._pool = pool
        self._connection = connection
        self._closed = False
        self._lock = Lock()

    def __getattr__(self, name):
        if self._closed:
            raise RuntimeError("Pooled connection has been released")
        return getattr(self._connection, name)

    @property
    def closed(self):
        return self._closed

    def close(self):
        with self._lock:
            if self._closed:
                return

            self._closed = True

            try:
                if not self._connection.closed:
                    # End any unfinished transaction.
                    self._connection.rollback()

                    # DISCARD ALL must run outside a transaction.
                    previous_autocommit = self._connection.autocommit
                    try:
                        self._connection.autocommit = True
                        with self._connection.cursor() as cursor:
                            cursor.execute("DISCARD ALL")
                    finally:
                        self._connection.autocommit = previous_autocommit

            except Exception:
                # Never reuse a connection whose cleanup failed.
                try:
                    self._connection.close()
                finally:
                    self._pool.putconn(self._connection)
                raise
            else:
                self._pool.putconn(self._connection)
