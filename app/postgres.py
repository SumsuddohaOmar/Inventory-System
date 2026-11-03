"""PostgreSQL connection support for the isolated cloud pilot."""

from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PostgresTarget:
    dsn: str = field(repr=False)


class Row(dict):
    """Support the named and positional access used by existing SQL callers."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def row_factory(cursor):
    columns = tuple(column.name for column in cursor.description or ())
    return lambda values: Row(zip(columns, values))


class Connection:
    # The enclosing connection context already owns a database transaction.
    in_transaction = True

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        if sql in ("BEGIN", "BEGIN IMMEDIATE"):
            return None
        # Application queries use question marks only as bind placeholders.
        if parameters:
            return self.connection.execute(sql.replace("?", "%s"), parameters)
        return self.connection.execute(sql)


@contextmanager
def connect(target):
    import psycopg

    with psycopg.connect(target.dsn, row_factory=row_factory, connect_timeout=5) as connection:
        connection.execute("SET LOCAL lock_timeout = '5s'")
        # Serialize pilot transactions to preserve SQLite's stock write ordering.
        # This is intentionally coarse and applies only within one database.
        connection.execute("SELECT pg_advisory_xact_lock(171047112)")
        yield Connection(connection)
