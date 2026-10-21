"""SQL reader for GetTenderRequirements.

Seam: `SqlTenderRequirementsQuery.fetch`.
Skips when SQL Server is not configured. The tender id is fresh. Nothing is deleted.

T-QRY-1 — a detail row, two requirements, and one host name come back
in the tool's field names, requirements in position order.
T-QRY-2 — an id with no detail row returns None.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import bootstrap

bootstrap.init()

import pytest  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402
from app.events.db import transaction  # noqa: E402
from app.events.schema import create_schema  # noqa: E402
from app.queries.tender_requirements import SqlTenderRequirementsQuery  # noqa: E402


def _skip_unless_configured() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))
    if not settings.db_is_configured:
        pytest.skip(
            "SOMEE_DB_CONNECTION_STRING still holds the .env.example placeholder."
        )


def test_fetch_returns_published_tender_requirements() -> None:
    """T-QRY-1. Venue is position 1. Content is position 2. Alpha is 0.5."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    host_name = f"רינה-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO dbo.rm_tender_detail "
            "(tender_id, name, event_type, event_date, region, alpha) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            tender_id,
            "Summer Stage",
            "festival",
            "2026-07-01",
            "Tel Aviv",
            0.5,
        )
        cursor.execute(
            "INSERT INTO dbo.rm_requirement "
            "(tender_id, requirement_id, position, type, weight, is_threshold, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            tender_id,
            "req-content",
            2,
            "CONTENT",
            60,
            0,
            "Evening program",
        )
        cursor.execute(
            "INSERT INTO dbo.rm_requirement "
            "(tender_id, requirement_id, position, type, weight, is_threshold, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            tender_id,
            "req-venue",
            1,
            "VENUE",
            40,
            1,
            "Hall for 5000",
        )
        cursor.execute("INSERT INTO dbo.approved_hosts (name) VALUES (?)", host_name)
        cursor.close()

    result = SqlTenderRequirementsQuery().fetch(tender_id)

    assert result["tender"] == {
        "name": "Summer Stage",
        "type": "festival",
        "event_date": "2026-07-01",
        "region": "Tel Aviv",
        "alpha": 0.5,
    }
    assert result["requirements"] == [
        {
            "id": "req-venue",
            "type": "VENUE",
            "weight": 40,
            "is_threshold": True,
            "description": "Hall for 5000",
        },
        {
            "id": "req-content",
            "type": "CONTENT",
            "weight": 60,
            "is_threshold": False,
            "description": "Evening program",
        },
    ]
    assert host_name in result["approved_hosts"]


def test_fetch_returns_none_for_an_unknown_tender() -> None:
    """T-QRY-2. The id is fresh and has no detail row."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)

    assert SqlTenderRequirementsQuery().fetch(tender_id) is None
