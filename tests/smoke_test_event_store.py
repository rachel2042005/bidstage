"""Event store round-trip smoke test.

Standalone:  python tests/smoke_test_event_store.py
Under pytest: python -m pytest   (pytest.ini collects smoke_test_*.py)

Each run uses a fresh random stream_id and leaves its rows behind, because the
events table is append-only (NFR-ES-2) and this test will not delete from it.
A few hundred bytes against a 30 MB budget (ENV-2) is immaterial; re-running
init_db.py on a fresh database is how rows get cleared.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Run as a script, Python puts tests/ on sys.path rather than the repo root, so
# `import app` fails. pytest does not need this (pytest.ini sets pythonpath).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from app import bootstrap  # noqa: E402

bootstrap.init()

import pyodbc  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402
from app.events import ConcurrencyError, EventStore  # noqa: E402
from app.events.db import available_sql_server_drivers, transaction  # noqa: E402
from app.events.event_types import TENDER_CREATED  # noqa: E402
from app.events.schema import create_schema  # noqa: E402

# Hebrew text is included deliberately: event_data is NVARCHAR(MAX) and the
# round trip is what proves the column type and the JSON encoder agree.
EVENT_DATA = {
    "name": "טקס פרסי התרבות השנתי",
    "event_type": "culture",
    "event_date": "2026-11-20",
    "region": "תל אביב",
    "estimated_budget": 90000000,
    "currency": "ILS",
    "deadline": "2026-10-30T23:59:59Z",
    "alpha": 0.6,
    "organizer_id": "org-0001",
}

EVENT_METADATA = {
    "actor": "org-0001",
    "actor_role": "organizer",
    "correlation_id": "smoke-test",
}


def _skip_unless_configured() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))
    if not settings.db_is_configured:
        pytest.skip(
            "SOMEE_DB_CONNECTION_STRING still holds the .env.example placeholder."
        )


def test_round_trip() -> None:
    """Append TenderCreated, read the stream back, assert the payload matches."""
    _skip_unless_configured()
    stream_id = f"tender-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)
        version = EventStore(conn).append(
            stream_id, 0, TENDER_CREATED, EVENT_DATA, EVENT_METADATA
        )
    assert version == 1, "TenderCreated must be version 1 of its stream (INV-9)"

    with transaction() as conn:
        events = EventStore(conn).read_stream(stream_id)

    assert len(events) == 1
    event = events[0]
    assert event.stream_id == stream_id
    assert event.version == 1
    assert event.event_type == TENDER_CREATED
    assert event.global_seq > 0
    assert event.created_at is not None
    assert isinstance(event.event_id, uuid.UUID)

    # Full equality, so a silent encoding change cannot pass.
    assert event.event_data == EVENT_DATA
    assert event.metadata == EVENT_METADATA
    assert event.event_data["name"] == "טקס פרסי התרבות השנתי"
    assert event.event_data["alpha"] == 0.6


def test_version_conflict_is_rejected() -> None:
    """UNIQUE (stream_id, version) is the optimistic concurrency check (NFR-ES-4)."""
    _skip_unless_configured()
    stream_id = f"tender-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)
        EventStore(conn).append(stream_id, 0, TENDER_CREATED, EVENT_DATA)

    with pytest.raises(ConcurrencyError):
        with transaction() as conn:
            # A second writer that also believed the stream was empty.
            EventStore(conn).append(stream_id, 0, TENDER_CREATED, EVENT_DATA)


def _run_as_script() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"FAILED  configuration error: {exc}")
        return 2
    if not settings.db_is_configured:
        print("SKIPPED  SOMEE_DB_CONNECTION_STRING is still the placeholder.")
        print("         Fill in .env, run python init_db.py, then re-run this.")
        return 2

    stream_id = f"tender-{uuid.uuid4()}"
    print(f"Connecting to Somee...  drivers: {available_sql_server_drivers()}")

    try:
        with transaction() as conn:
            create_schema(conn)
            version = EventStore(conn).append(
                stream_id, 0, TENDER_CREATED, EVENT_DATA, EVENT_METADATA
            )
        print(f"Appended {TENDER_CREATED} to {stream_id} at version {version}.")

        with transaction() as conn:
            events = EventStore(conn).read_stream(stream_id)
    except pyodbc.Error as exc:
        sqlstate = exc.args[0] if exc.args else "?"
        print(f"FAILED  database error (SQLSTATE {sqlstate}): {exc}")
        return 1

    if not events:
        print("FAILED  stream read back empty.")
        return 1

    event = events[0]
    data_ok = event.event_data == EVENT_DATA
    meta_ok = event.metadata == EVENT_METADATA

    print("\n--- event read back ---")
    print(f"  global_seq : {event.global_seq}")
    print(f"  event_id   : {event.event_id}")
    print(f"  stream_id  : {event.stream_id}")
    print(f"  version    : {event.version}")
    print(f"  event_type : {event.event_type}")
    print(f"  created_at : {event.created_at}")
    print(f"  name (he)  : {event.event_data['name']}")
    print(f"  metadata   : {event.metadata}")

    if data_ok and meta_ok:
        print("\nSUCCESS  payload and metadata match exactly.")
        return 0

    print("\nFAILED  payload mismatch.")
    if not data_ok:
        print(f"  expected event_data: {EVENT_DATA}")
        print(f"  actual   event_data: {event.event_data}")
    if not meta_ok:
        print(f"  expected metadata: {EVENT_METADATA}")
        print(f"  actual   metadata: {event.metadata}")
    return 1


if __name__ == "__main__":
    sys.exit(_run_as_script())
