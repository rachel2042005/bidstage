"""GetTenderRequirements (`02-architecture.md` §4 and §7).

`SqlTenderRequirementsQuery.fetch` reads `rm_tender_detail`, `rm_requirement`,
and `approved_hosts`. It does not read the events table (`NFR-CQRS-2`, `T-QRY-1`).
`PublishTender` fills the tender rows later.
"""

from __future__ import annotations

from typing import Protocol

import pyodbc

_SELECT_TENDER = """
SELECT name, event_type, event_date, region, alpha
FROM dbo.rm_tender_detail
WHERE tender_id = ?
"""

_SELECT_REQUIREMENTS = """
SELECT requirement_id, type, weight, is_threshold, description
FROM dbo.rm_requirement
WHERE tender_id = ?
ORDER BY position ASC
"""

_SELECT_HOSTS = """
SELECT name
FROM dbo.approved_hosts
ORDER BY name ASC
"""


class TenderRequirementsQuery(Protocol):
    def fetch(self, tender_id: str) -> dict | None:
        """`{tender, requirements, approved_hosts}`, or None when absent.

        The view may carry columns the tool must not return, including price.
        """
        ...


class SqlTenderRequirementsQuery:
    """SQL reader for `GetTenderRequirements` (`T-QRY-1`)."""

    def fetch(self, tender_id: str) -> dict | None:
        from app.events.db import transaction

        with transaction() as conn:
            return _fetch(conn, tender_id)


def _fetch(conn: pyodbc.Connection, tender_id: str) -> dict | None:
    cursor = conn.cursor()
    cursor.execute(_SELECT_TENDER, tender_id)
    tender = cursor.fetchone()
    if tender is None:
        cursor.close()
        return None
    cursor.execute(_SELECT_REQUIREMENTS, tender_id)
    requirements = cursor.fetchall()
    cursor.execute(_SELECT_HOSTS)
    hosts = cursor.fetchall()
    cursor.close()
    return {
        "tender": {
            "name": tender[0],
            "type": tender[1],
            "event_date": tender[2],
            "region": tender[3],
            "alpha": tender[4],
        },
        "requirements": [
            {
                "id": row[0],
                "type": row[1],
                "weight": row[2],
                "is_threshold": bool(row[3]),
                "description": row[4],
            }
            for row in requirements
        ],
        "approved_hosts": [row[0] for row in hosts],
    }
