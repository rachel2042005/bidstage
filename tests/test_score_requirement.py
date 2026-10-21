"""SQL handler for an intermediate requirement score.

Seam: `SqlScoreRequirementHandler.handle`.
Skips when SQL Server is not configured. Stream ids are fresh. Nothing is deleted.

T-CMD-20 — a closed tender and a submitted bid. Score 9 appends
RequirementScored at the next version and stores weight 40.
T-AG-4 — a second score for the same pair keeps version 2 and score 9.
T-INV-2 — scoring before TenderClosedForBids appends nothing.
T-INV-4 — scoring after PricesRevealed appends nothing.
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

from app.commands.score_requirement import (  # noqa: E402
    ScoreRequirement,
    ScoringRejected,
    SqlScoreRequirementHandler,
)
from app.config import ConfigError, load_settings  # noqa: E402
from app.events import EventStore  # noqa: E402
from app.events.db import transaction  # noqa: E402
from app.events.event_types import (  # noqa: E402
    BID_SUBMITTED,
    PRICE_OFFER_SEALED,
    PRICES_REVEALED,
    REQUIREMENT_SCORED,
    TENDER_CLOSED_FOR_BIDS,
    TENDER_PUBLISHED,
)
from app.events.schema import create_schema  # noqa: E402


def _skip_unless_configured() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))
    if not settings.db_is_configured:
        pytest.skip(
            "SOMEE_DB_CONNECTION_STRING still holds the .env.example placeholder."
        )


def test_score_appends_requirement_scored_and_projects_weight() -> None:
    """T-CMD-20. Weight 40 is the requirement row. Score 9 is the command."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"
    command = ScoreRequirement(
        bid_id=bid_id,
        requirement_id="req-venue",
        score=9,
        justification="The hall seats 5000.",
        sources=("https://example.com/hall",),
        rubric_version="venue-1",
    )

    with transaction() as conn:
        create_schema(conn)
        cursor = conn.cursor()
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
        cursor.close()
        EventStore(conn).append(
            tender_id,
            0,
            TENDER_CLOSED_FOR_BIDS,
            {"closed_at": "2026-06-02T00:00:00Z", "bid_count": 1},
        )
        EventStore(conn).append(
            bid_id,
            0,
            BID_SUBMITTED,
            {
                "tender_id": tender_id,
                "supplier_id": "sup-1",
                "concept": "ceremony",
                "responses": [{"requirement_id": "req-venue", "answer": "Hall for 5000"}],
            },
        )
        result = SqlScoreRequirementHandler(conn).handle(command)

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT requirement_id, score, weight FROM dbo.rm_requirement_score "
            "WHERE bid_id = ?",
            bid_id,
        )
        scores = cursor.fetchall()
        cursor.close()

    assert result == {"accepted": True, "event_version": 2}
    assert [(event.version, event.event_type) for event in events] == [
        (1, BID_SUBMITTED),
        (2, REQUIREMENT_SCORED),
    ]
    assert events[1].event_data == {
        "requirement_id": "req-venue",
        "score": 9,
        "justification": "The hall seats 5000.",
        "sources": ["https://example.com/hall"],
        "scored_by": "agent",
        "rubric_version": "venue-1",
    }
    assert [(row[0], row[1], row[2]) for row in scores] == [("req-venue", 9, 40)]


def test_second_score_for_the_same_pair_appends_nothing() -> None:
    """T-AG-4. The retry says score 4. The stored score stays 9 at version 2."""
    _skip_unless_configured()
    _tender_id, bid_id = _open_for_scoring()
    first = ScoreRequirement(
        bid_id=bid_id,
        requirement_id="req-venue",
        score=9,
        justification="The hall seats 5000.",
        sources=("https://example.com/hall",),
        rubric_version="venue-1",
    )
    retry = ScoreRequirement(
        bid_id=bid_id,
        requirement_id="req-venue",
        score=4,
        justification="A different reading.",
        sources=("https://example.com/other",),
        rubric_version="venue-2",
    )

    with transaction() as conn:
        handler = SqlScoreRequirementHandler(conn)
        first_result = handler.handle(first)
        second_result = handler.handle(retry)

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT requirement_id, score, weight FROM dbo.rm_requirement_score "
            "WHERE bid_id = ?",
            bid_id,
        )
        scores = cursor.fetchall()
        cursor.close()

    assert first_result == {"accepted": True, "event_version": 2}
    assert second_result == {"accepted": True, "event_version": 2}
    assert [event.event_type for event in events] == [BID_SUBMITTED, REQUIREMENT_SCORED]
    assert events[1].event_data["score"] == 9
    assert events[1].event_data["rubric_version"] == "venue-1"
    assert [(row[0], row[1], row[2]) for row in scores] == [("req-venue", 9, 40)]


def test_score_before_close_appends_nothing() -> None:
    """T-INV-2. The tender is published and still open. The bid stays submitted."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"

    with transaction() as conn:
        _requirement(conn, tender_id)
        EventStore(conn).append(
            tender_id,
            0,
            TENDER_PUBLISHED,
            {"published_at": "2026-06-01T00:00:00Z"},
        )
        _submit(conn, bid_id, tender_id)

    with pytest.raises(ScoringRejected):
        with transaction() as conn:
            SqlScoreRequirementHandler(conn).handle(_command(bid_id))

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        score_count = _score_count(conn, bid_id)

    assert [event.event_type for event in events] == [BID_SUBMITTED]
    assert score_count == 0


def test_score_after_prices_revealed_appends_nothing() -> None:
    """T-INV-4. Prices are revealed at 900000. No RequirementScored is appended."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"

    with transaction() as conn:
        _requirement(conn, tender_id)
        store = EventStore(conn)
        store.append(
            tender_id,
            0,
            TENDER_CLOSED_FOR_BIDS,
            {"closed_at": "2026-06-02T00:00:00Z", "bid_count": 1},
        )
        store.append(
            tender_id,
            1,
            PRICES_REVEALED,
            {"prices": [{"bid_id": bid_id, "amount": 900_000}]},
        )
        _submit(conn, bid_id, tender_id)
        store.append(
            bid_id,
            1,
            PRICE_OFFER_SEALED,
            {"price_amount": 900_000, "currency": "ILS", "sealed_at": "2026-06-01T09:00:00Z"},
        )

    with pytest.raises(ScoringRejected):
        with transaction() as conn:
            SqlScoreRequirementHandler(conn).handle(_command(bid_id))

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        score_count = _score_count(conn, bid_id)

    assert [event.event_type for event in events] == [BID_SUBMITTED, PRICE_OFFER_SEALED]
    assert score_count == 0
    assert events[1].event_data["price_amount"] == 900_000


def _open_for_scoring() -> tuple[str, str]:
    tender_id = f"tender-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"
    with transaction() as conn:
        _requirement(conn, tender_id)
        EventStore(conn).append(
            tender_id,
            0,
            TENDER_CLOSED_FOR_BIDS,
            {"closed_at": "2026-06-02T00:00:00Z", "bid_count": 1},
        )
        _submit(conn, bid_id, tender_id)
    return tender_id, bid_id


def _requirement(conn, tender_id: str) -> None:
    create_schema(conn)
    cursor = conn.cursor()
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
    cursor.close()


def _submit(conn, bid_id: str, tender_id: str) -> None:
    EventStore(conn).append(
        bid_id,
        0,
        BID_SUBMITTED,
        {
            "tender_id": tender_id,
            "supplier_id": "sup-1",
            "concept": "ceremony",
            "responses": [{"requirement_id": "req-venue", "answer": "Hall for 5000"}],
        },
    )


def _command(bid_id: str) -> ScoreRequirement:
    return ScoreRequirement(
        bid_id=bid_id,
        requirement_id="req-venue",
        score=9,
        justification="The hall seats 5000.",
        sources=("https://example.com/hall",),
        rubric_version="venue-1",
    )


def _score_count(conn, bid_id: str) -> int:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM dbo.rm_requirement_score WHERE bid_id = ?",
        bid_id,
    )
    count = int(cursor.fetchone()[0])
    cursor.close()
    return count
