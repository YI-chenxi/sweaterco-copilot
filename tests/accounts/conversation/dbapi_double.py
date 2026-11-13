"""Scripted DB-API double: NOT PostgreSQL and NOT a durability/concurrency proof."""


class Database:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.fail_commit = False

    def connect(self):
        return Connection(self)

    def done(self):
        assert not self.steps, self.steps
        assert self.closes == 1


class Connection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return Cursor(self.database)

    def commit(self):
        self.database.commits += 1
        if self.database.fail_commit:
            raise RuntimeError("SECRET database acknowledgement lost")

    def rollback(self):
        self.database.rollbacks += 1

    def close(self):
        self.database.closes += 1


class Cursor:
    def __init__(self, database):
        self.database = database
        self.rows = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, parameters=()):
        normalized = " ".join(query.split())
        self.database.calls.append((normalized, parameters))
        assert self.database.steps, normalized
        fragment, response = self.database.steps.pop(0)
        assert fragment in normalized, (fragment, normalized)
        if isinstance(response, Exception):
            raise response
        self.rows = list(response)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows
