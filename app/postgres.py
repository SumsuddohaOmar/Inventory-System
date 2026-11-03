"""PostgreSQL connection support for the isolated cloud pilot."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock

_pools = {}
_pool_lock = Lock()


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
    from psycopg_pool import ConnectionPool

    with _pool_lock:
        if target not in _pools:
            _pools[target] = ConnectionPool(
                target.dsn, min_size=0, max_size=4, max_idle=60,
                timeout=10, open=True,
                kwargs={"row_factory": row_factory, "connect_timeout": 5},
            )
        pool = _pools[target]
    with pool.connection() as connection:
        connection.execute("SET LOCAL lock_timeout = '5s'")
        # Serialize pilot transactions to preserve SQLite's stock write ordering.
        # This is intentionally coarse and applies only within one database.
        connection.execute("SELECT pg_advisory_xact_lock(171047112)")
        yield Connection(connection)
