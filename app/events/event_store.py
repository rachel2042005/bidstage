"""Append-only event store. See specs/02-architecture.md §5.

No method issues UPDATE or DELETE against dbo.events (NFR-ES-2). Corrections
are made by appending a compensating event, never by editing history.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

import pyodbc

_STREAM_VERSION_CONSTRAINT = "uq_events_stream_version"
_EVENT_ID_CONSTRAINT = "uq_events_event_id"


class ConcurrencyError(RuntimeError):
    """Another writer already wrote this (stream_id, version)."""


class DuplicateEventError(RuntimeError):
    """This event_id was already appended."""


@dataclass(frozen=True)
class NewEvent:
    event_type: str
    event_data: dict[str, Any]
    metadata: dict[str, Any] | None = None
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True)
class StoredEvent:
    global_seq: int
    event_id: uuid.UUID
    stream_id: str
    version: int
    event_type: str
    event_data: dict[str, Any]
    metadata: dict[str, Any] | None
    created_at: datetime


_INSERT = """
INSERT INTO dbo.events (event_id, stream_id, version, event_type, event_data, metadata)
VALUES (?, ?, ?, ?, ?, ?)
"""

_COLUMNS = (
    "global_seq, event_id, stream_id, version, event_type, "
    "event_data, metadata, created_at"
)

_SELECT_STREAM = f"""
SELECT {_COLUMNS} FROM dbo.events WHERE stream_id = ? ORDER BY version
"""

_SELECT_ALL = f"""
SELECT {_COLUMNS} FROM dbo.events WHERE global_seq > ? ORDER BY global_seq
"""

_SELECT_LAST_VERSION = (
    "SELECT COALESCE(MAX(version), 0) FROM dbo.events WHERE stream_id = ?"
)


class EventStore:
    """Wraps a connection so callers control the transaction boundary."""

    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def last_version(self, stream_id: str) -> int:
        cursor = self._conn.cursor()
        cursor.execute(_SELECT_LAST_VERSION, stream_id)
        version = int(cursor.fetchone()[0])
        cursor.close()
        return version

    def append(
        self,
        stream_id: str,
        expected_version: int,
        event_type: str,
        event_data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Appends one event. Returns the version written."""
        return self.append_many(
            stream_id,
            expected_version,
            [NewEvent(event_type, event_data, metadata)],
        )

    def append_many(
        self,
        stream_id: str,
        expected_version: int,
        events: Sequence[NewEvent],
    ) -> int:
        """Appends events consecutively. Returns the last version written.

        Used where ordering must be atomic, such as BidSubmitted immediately
        followed by PriceOfferSealed (INV-1).
        """
        if not events:
            return expected_version

        cursor = self._conn.cursor()
        # event_data and metadata are NVARCHAR(MAX); without this, long Hebrew
        # justifications can bind as a narrower type and truncate.
        cursor.setinputsizes(
            [
                None,
                None,
                None,
                None,
                (pyodbc.SQL_WVARCHAR, 0, 0),
                (pyodbc.SQL_WVARCHAR, 0, 0),
            ]
        )

        version = expected_version
        try:
            for event in events:
                version += 1
                cursor.execute(
                    _INSERT,
                    str(event.event_id),
                    stream_id,
                    version,
                    event.event_type,
                    _dump(event.event_data),
                    _dump(event.metadata) if event.metadata is not None else None,
                )
        except pyodbc.IntegrityError as exc:
            message = str(exc)
            if _STREAM_VERSION_CONSTRAINT in message:
                raise ConcurrencyError(
                    f"stream {stream_id} already has version {version}; "
                    "reload the aggregate and retry"
                ) from exc
            if _EVENT_ID_CONSTRAINT in message:
                raise DuplicateEventError(
                    "event_id already appended; this retry was a duplicate"
                ) from exc
            raise
        finally:
            cursor.close()

        return version

    def read_stream(self, stream_id: str) -> list[StoredEvent]:
        cursor = self._conn.cursor()
        cursor.execute(_SELECT_STREAM, stream_id)
        events = [_to_stored_event(row) for row in cursor.fetchall()]
        cursor.close()
        return events

    def read_all(self, after_global_seq: int = 0) -> list[StoredEvent]:
        """Global order, for rebuilding read models from scratch."""
        cursor = self._conn.cursor()
        cursor.execute(_SELECT_ALL, after_global_seq)
        events = [_to_stored_event(row) for row in cursor.fetchall()]
        cursor.close()
        return events


def _dump(payload: dict[str, Any]) -> str:
    # ensure_ascii=False keeps Hebrew as characters, not \uXXXX escapes.
    return json.dumps(payload, ensure_ascii=False)


def _to_stored_event(row: pyodbc.Row) -> StoredEvent:
    return StoredEvent(
        global_seq=int(row[0]),
        event_id=uuid.UUID(str(row[1])),
        stream_id=str(row[2]),
        version=int(row[3]),
        event_type=str(row[4]),
        event_data=json.loads(row[5]),
        metadata=json.loads(row[6]) if row[6] is not None else None,
        created_at=row[7],
    )
