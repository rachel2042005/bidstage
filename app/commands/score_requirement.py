"""ScoreRequirement command (`02-architecture.md` §3 and §7, `T-CMD-20`).

The handler replays the bid stream, checks the scoring invariants, appends
`RequirementScored`, and projects `rm_requirement_score` in that same
transaction. Weight comes from `rm_requirement`. A second score for the same
pair returns the stored version (`T-AG-4`). It never reads `price_amount`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pyodbc

from app.events.event_store import EventStore, StoredEvent
from app.events.event_types import (
    BID_DISQUALIFIED,
    BID_SUBMITTED,
    BID_WITHDRAWN,
    PRICES_REVEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    REQUIREMENT_SCORED,
    TENDER_CANCELLED,
    TENDER_CLOSED_FOR_BIDS,
    TENDER_FAILED,
)

_SELECT_WEIGHT = """
SELECT weight
FROM dbo.rm_requirement
WHERE tender_id = ? AND requirement_id = ?
"""

_INSERT_SCORE = """
INSERT INTO dbo.rm_requirement_score (bid_id, requirement_id, score, weight)
VALUES (?, ?, ?, ?)
"""

_BID_TERMINAL = frozenset(
    {BID_WITHDRAWN, BID_DISQUALIFIED, QUALITY_ASSESSMENT_COMPLETED}
)
_TENDER_CLOSED = frozenset({PRICES_REVEALED, TENDER_FAILED, TENDER_CANCELLED})


@dataclass(frozen=True)
class ScoreRequirement:
    """The command the tool dispatches. `scored_by` is always `"agent"`."""

    bid_id: str
    requirement_id: str
    score: int
    justification: str
    sources: tuple[str, ...]
    rubric_version: str
    scored_by: str = "agent"


class ScoreRequirementHandler(Protocol):
    def handle(self, command: ScoreRequirement) -> dict:
        """Return `{"accepted": bool, "event_version": int}`."""
        ...


class ScoringRejected(Exception):
    """The bid cannot be scored. The tool leaves this exception unchanged."""


class SqlScoreRequirementHandler:
    """Append one RequirementScored and project its weight (`T-CMD-20`)."""

    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def handle(self, command: ScoreRequirement) -> dict:
        _require_score(command)
        store = EventStore(self._conn)
        bid_events = store.read_stream(command.bid_id)
        existing = _scored_version(bid_events, command.requirement_id)
        if existing is not None:
            return {"accepted": True, "event_version": existing}
        if any(event.event_type in _BID_TERMINAL for event in bid_events):
            raise ScoringRejected(f"{command.bid_id} is not open for scoring.")
        tender_id = _tender_id(bid_events)
        if tender_id is None:
            raise ScoringRejected(f"{command.bid_id} has no BidSubmitted.")
        _require_under_evaluation(store.read_stream(tender_id), tender_id)
        weight = _weight(self._conn, tender_id, command.requirement_id)
        version = store.append(
            command.bid_id,
            store.last_version(command.bid_id),
            REQUIREMENT_SCORED,
            {
                "requirement_id": command.requirement_id,
                "score": command.score,
                "justification": command.justification,
                "sources": list(command.sources),
                "scored_by": command.scored_by,
                "rubric_version": command.rubric_version,
            },
        )
        _insert_score(
            self._conn,
            command.bid_id,
            command.requirement_id,
            command.score,
            weight,
        )
        return {"accepted": True, "event_version": version}


def _require_score(command: ScoreRequirement) -> None:
    score = command.score
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 10:
        raise ScoringRejected("score must be an integer from 0 to 10.")
    if not isinstance(command.requirement_id, str) or not command.requirement_id.strip():
        raise ScoringRejected("requirement_id is required.")


def _scored_version(events: list[StoredEvent], requirement_id: str) -> int | None:
    for event in events:
        if event.event_type != REQUIREMENT_SCORED:
            continue
        if event.event_data.get("requirement_id") == requirement_id:
            return event.version
    return None


def _tender_id(events: list[StoredEvent]) -> str | None:
    for event in events:
        if event.event_type != BID_SUBMITTED:
            continue
        tender_id = event.event_data.get("tender_id")
        if isinstance(tender_id, str) and tender_id.strip():
            return tender_id
    return None


def _require_under_evaluation(events: list[StoredEvent], tender_id: str) -> None:
    types = {event.event_type for event in events}
    if TENDER_CLOSED_FOR_BIDS not in types:
        raise ScoringRejected(f"{tender_id} is not closed for bids.")
    if types & _TENDER_CLOSED:
        raise ScoringRejected(f"{tender_id} is no longer under evaluation.")


def _weight(conn: pyodbc.Connection, tender_id: str, requirement_id: str) -> int:
    cursor = conn.cursor()
    cursor.execute(_SELECT_WEIGHT, tender_id, requirement_id)
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        raise ScoringRejected(
            f"requirement {requirement_id} is not on tender {tender_id}."
        )
    return int(row[0])


def _insert_score(
    conn: pyodbc.Connection,
    bid_id: str,
    requirement_id: str,
    score: int,
    weight: int,
) -> None:
    cursor = conn.cursor()
    cursor.execute(_INSERT_SCORE, bid_id, requirement_id, score, weight)
    cursor.close()
