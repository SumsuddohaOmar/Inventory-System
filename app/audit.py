"""Append business change snapshots in the SAME transaction as the change.

The actor context is set by authenticated middleware and follows FastAPI's
worker-thread context. Domain tests default to the explicit local-owner actor.
This audit records business changes, never passwords or session tokens.
"""

from contextvars import ContextVar
from datetime import datetime, timezone
import json
from uuid import uuid4

actor = ContextVar("inventory_actor", default="local-owner")


def record(connection, action: str, entity_id: str, before=None, after=None):
    """Append one timestamped change; any enclosing rollback removes it too."""
    connection.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid4()), actor.get(), action, entity_id,
         json.dumps(before, ensure_ascii=False) if before is not None else None,
         json.dumps(after, ensure_ascii=False) if after is not None else None,
         datetime.now(timezone.utc).isoformat()),
    )
