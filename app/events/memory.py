"""In-memory EventStore for tests and local runs without SQL Server.

Same append-only rules as the pyodbc store: no UPDATE/DELETE, version must
increment by 1, duplicate event_id is rejected.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from app.events.event_store import (
    ConcurrencyError,
    DuplicateEventError,
    NewEvent,
    StoredEvent,
)


class InMemoryEventStore:
    def __init__(self) -> None:
        self._streams: dict[str, list[StoredEvent]] = {}
        self._ids: set[uuid.UUID] = set()
        self._seq = 0

    def last_version(self, stream_id: str) -> int:
        events = self._streams.get(stream_id) or []
        return events[-1].version if events else 0

    def append(
        self,
        stream_id: str,
        expected_version: int,
        event_type: str,
        event_data: dict,
        metadata: dict | None = None,
    ) -> int:
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
        if not events:
            return expected_version
        if self.last_version(stream_id) != expected_version:
            raise ConcurrencyError(
                f"stream {stream_id} already has version "
                f"{self.last_version(stream_id)}; reload the aggregate and retry"
            )
        for event in events:
            if event.event_id in self._ids:
                raise DuplicateEventError(
                    "event_id already appended; this retry was a duplicate"
                )
        version = expected_version
        written: list[StoredEvent] = []
        for event in events:
            version += 1
            self._seq += 1
            stored = StoredEvent(
                global_seq=self._seq,
                event_id=event.event_id,
                stream_id=stream_id,
                version=version,
                event_type=event.event_type,
                event_data=event.event_data,
                metadata=event.metadata,
                created_at=datetime.now(timezone.utc),
            )
            written.append(stored)
            self._ids.add(event.event_id)
        self._streams.setdefault(stream_id, []).extend(written)
        return version

    def read_stream(self, stream_id: str) -> list[StoredEvent]:
        return list(self._streams.get(stream_id) or [])

    def read_all(self, after_global_seq: int = 0) -> list[StoredEvent]:
        all_events = [e for events in self._streams.values() for e in events]
        all_events.sort(key=lambda e: e.global_seq)
        return [e for e in all_events if e.global_seq > after_global_seq]
