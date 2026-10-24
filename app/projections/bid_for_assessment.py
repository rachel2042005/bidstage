"""Agent work queue. The row is `BidSubmitted` content and has no price.

`enqueue_bid_for_assessment` inserts one pending row.
`SqlAssessmentQueue.claim` locks the oldest pending row, marks it claimed,
and commits before returning (`T-AG-39`, `T-AG-11`).
`release_stalled_claims` returns a claimed row to pending when `claimed_at`
is strictly older than the timeout. The caller commits (`T-AG-43`, `T-AG-44`).
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import pyodbc

from agent.runner import AssessmentWork

_INSERT = """
INSERT INTO dbo.rm_bid_for_assessment (
    bid_id, tender_id, supplier_id, concept, responses_json, status
) VALUES (?, ?, ?, ?, ?, 'pending')
"""

_CLAIM = """
WITH next_bid AS (
    SELECT TOP (1) bid_id
    FROM dbo.rm_bid_for_assessment WITH (UPDLOCK, READPAST, ROWLOCK)
    WHERE status = 'pending'
    ORDER BY queued_at, bid_id
)
UPDATE q
SET status = 'claimed', claimed_at = SYSUTCDATETIME()
OUTPUT
    inserted.bid_id,
    inserted.tender_id,
    inserted.supplier_id,
    inserted.responses_json
FROM dbo.rm_bid_for_assessment AS q
INNER JOIN next_bid ON q.bid_id = next_bid.bid_id
"""

_RELEASE_STALLED = """
UPDATE dbo.rm_bid_for_assessment
SET status = 'pending', claimed_at = NULL
WHERE status = 'claimed'
  AND claimed_at < DATEADD(second, ?, SYSUTCDATETIME())
"""


def enqueue_bid_for_assessment(
    conn: pyodbc.Connection,
    bid_id: str,
    tender_id: str,
    supplier_id: str,
    concept: str,
    responses: Sequence[Mapping[str, str]],
) -> None:
    """Insert one pending row. The arguments are `BidSubmitted` fields."""
    cursor = conn.cursor()
    cursor.setinputsizes(
        [
            None,
            None,
            None,
            (pyodbc.SQL_WVARCHAR, 0, 0),
            (pyodbc.SQL_WVARCHAR, 0, 0),
        ]
    )
    cursor.execute(
        _INSERT,
        bid_id,
        tender_id,
        supplier_id,
        concept,
        json.dumps(list(responses), ensure_ascii=False),
    )
    cursor.close()


def release_stalled_claims(conn: pyodbc.Connection, timeout_seconds: int) -> None:
    """Return stalled claims to pending. The caller commits."""
    cursor = conn.cursor()
    cursor.execute(_RELEASE_STALLED, -timeout_seconds)
    cursor.close()


class SqlAssessmentQueue:
    """Operational take. Commits the claim before the caller starts scoring."""

    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def claim(self) -> AssessmentWork | None:
        cursor = self._conn.cursor()
        cursor.execute(_CLAIM)
        row = cursor.fetchone()
        cursor.close()
        self._conn.commit()
        if row is None:
            return None
        return AssessmentWork(
            bid_id=str(row[0]),
            tender_id=str(row[1]),
            supplier_id=str(row[2]),
            answers=_answers(json.loads(row[3])),
        )


def _answers(responses: Any) -> dict[str, str]:
    return {item["requirement_id"]: item["answer"] for item in responses}
