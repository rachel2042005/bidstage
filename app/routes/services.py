"""Per-request unit of work: event store, users, and tender projections.

When the app is constructed with in-memory collaborators (tests), those are
used. Otherwise SQL Server is opened and schema is ensured. Event append and
projection share that transaction (NFR-CQRS-3).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from flask import current_app

from app.events.db import transaction
from app.events.event_store import EventStore
from app.events.schema import create_schema
from app.projections.tenders import SqlTendersProjection, create_tenders_projection
from app.projections.users import SqlUsersProjection, create_users_projection


@contextmanager
def unit_of_work() -> Iterator[tuple]:
    injected = current_app.extensions.get("backend")
    if injected is not None:
        yield injected
        return
    with transaction() as conn:
        create_schema(conn)
        create_users_projection(conn)
        create_tenders_projection(conn)
        yield EventStore(conn), SqlUsersProjection(conn), SqlTendersProjection(conn)


auth_unit_of_work = unit_of_work
