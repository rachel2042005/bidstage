"""Terminal assessment command (`03-domain-events.md` §2, `05-agent-spec.md` §4).

Not an MCP tool. `SqlCompleteAssessmentHandler` appends
`QualityAssessmentCompleted` or `BidDisqualified` on the bid stream and
projects the assessment read models in that same transaction, and sets
`rm_bid_for_assessment.status` to `completed` when that row exists. It never
reads a price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pyodbc

from app.events.event_store import EventStore, StoredEvent
from app.events.event_types import (
    BID_DISQUALIFIED,
    BID_SUBMITTED,
    QUALITY_ASSESSMENT_COMPLETED,
)


@dataclass(frozen=True)
class RequirementScore:
    """One scored requirement on `QualityAssessmentCompleted`."""

    requirement_id: str
    score: int
    weight: int


@dataclass(frozen=True)
class QualityAssessmentCompleted:
    """Every requirement scored and quality is at least 60."""

    bid_id: str
    quality_score: float
    requirement_scores: tuple[RequirementScore, ...]


@dataclass(frozen=True)
class BidDisqualified:
    """Threshold failure or quality below 60.

    A threshold failure sets `failed_requirement_id` and leaves `quality_score`
    unset. A floor failure sets `quality_score` and leaves `failed_requirement_id`
    unset.
    """

    bid_id: str
    reason: str
    failed_requirement_id: str | None = None
    quality_score: float | None = None


class CompleteAssessmentHandler(Protocol):
    def handle(self, command: QualityAssessmentCompleted | BidDisqualified) -> None: ...


class AssessmentAlreadyTerminal(Exception):
    """The bid stream already has a terminal assessment event."""


_INSERT_ASSESSMENT = """
INSERT INTO dbo.rm_bid_assessment (
    bid_id, tender_id, status, quality_score, reason, failed_requirement_id
) VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_REQUIREMENT_SCORE = """
INSERT INTO dbo.rm_requirement_score (bid_id, requirement_id, score, weight)
VALUES (?, ?, ?, ?)
"""

_COMPLETE_QUEUE = """
UPDATE dbo.rm_bid_for_assessment
SET status = 'completed'
WHERE bid_id = ?
"""


class SqlCompleteAssessmentHandler:
    """Append one terminal event and project it. No price column is read."""

    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def handle(self, command: QualityAssessmentCompleted | BidDisqualified) -> None:
        store = EventStore(self._conn)
        events = store.read_stream(command.bid_id)
        if any(event.event_type in _TERMINAL for event in events):
            raise AssessmentAlreadyTerminal(
                f"{command.bid_id} already has a terminal assessment event."
            )
        tender_id = _tender_id(events)
        if isinstance(command, QualityAssessmentCompleted):
            store.append(
                command.bid_id,
                store.last_version(command.bid_id),
                QUALITY_ASSESSMENT_COMPLETED,
                _qualified_payload(command),
            )
            _insert_assessment(
                self._conn,
                command.bid_id,
                tender_id,
                "qualified",
                command.quality_score,
                None,
                None,
            )
            for item in command.requirement_scores:
                _insert_requirement_score(self._conn, command.bid_id, item)
        else:
            store.append(
                command.bid_id,
                store.last_version(command.bid_id),
                BID_DISQUALIFIED,
                _disqualified_payload(command),
            )
            _insert_assessment(
                self._conn,
                command.bid_id,
                tender_id,
                "disqualified",
                command.quality_score,
                command.reason,
                command.failed_requirement_id,
            )
        _complete_queue(self._conn, command.bid_id)


_TERMINAL = frozenset({QUALITY_ASSESSMENT_COMPLETED, BID_DISQUALIFIED})


def _tender_id(events: list[StoredEvent]) -> str | None:
    for event in events:
        if event.event_type == BID_SUBMITTED:
            tender_id = event.event_data.get("tender_id")
            if isinstance(tender_id, str) and tender_id.strip():
                return tender_id
    return None


def _qualified_payload(command: QualityAssessmentCompleted) -> dict:
    return {
        "quality_score": command.quality_score,
        "requirement_scores": [
            {
                "requirement_id": item.requirement_id,
                "score": item.score,
                "weight": item.weight,
            }
            for item in command.requirement_scores
        ],
    }


def _disqualified_payload(command: BidDisqualified) -> dict:
    payload: dict = {"reason": command.reason}
    if command.failed_requirement_id is not None:
        payload["failed_requirement_id"] = command.failed_requirement_id
    if command.quality_score is not None:
        payload["quality_score"] = command.quality_score
    return payload


def _insert_assessment(
    conn: pyodbc.Connection,
    bid_id: str,
    tender_id: str | None,
    status: str,
    quality_score: float | None,
    reason: str | None,
    failed_requirement_id: str | None,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        _INSERT_ASSESSMENT,
        bid_id,
        tender_id,
        status,
        quality_score,
        reason,
        failed_requirement_id,
    )
    cursor.close()


def _insert_requirement_score(
    conn: pyodbc.Connection, bid_id: str, item: RequirementScore
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        _INSERT_REQUIREMENT_SCORE,
        bid_id,
        item.requirement_id,
        item.score,
        item.weight,
    )
    cursor.close()


def _complete_queue(conn: pyodbc.Connection, bid_id: str) -> None:
    """Mark the queue row completed. Zero rows updated is success."""
    cursor = conn.cursor()
    cursor.execute(_COMPLETE_QUEUE, bid_id)
    cursor.close()
