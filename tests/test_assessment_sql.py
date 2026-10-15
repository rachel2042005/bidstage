"""SQL handlers for terminal assessment and reveal.

Seams: `SqlCompleteAssessmentHandler`, `SqlRevealPricesHandler`.
Skips when SQL Server is not configured. Stream ids are fresh. Nothing is deleted.

T-CMD-13 — quality 90.0 is the next bid version, status qualified, requirement rows match.
T-CMD-21 — a score row already stored is left in place when completion names it again.
T-CMD-14 — a threshold disqualification stores no quality and no requirement rows.
T-CMD-15 — a second terminal command raises and leaves the stream one event long.
T-CMD-16 — reveal with one bid still open raises and leaves the tender stream unchanged.
T-CMD-17 — the worked example reveals prices and ranks B first at 84.4.
T-CMD-18 — every bid disqualified appends TenderFailed and not PricesRevealed.
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

from app.commands.complete_assessment import (  # noqa: E402
    AssessmentAlreadyTerminal,
    BidDisqualified,
    QualityAssessmentCompleted,
    RequirementScore,
    SqlCompleteAssessmentHandler,
)
from app.commands.score_requirement import ScoreRequirement, SqlScoreRequirementHandler  # noqa: E402
from app.commands.reveal_prices import RevealRejected, SqlRevealPricesHandler  # noqa: E402
from app.config import ConfigError, load_settings  # noqa: E402
from app.events import EventStore  # noqa: E402
from app.events.db import transaction  # noqa: E402
from app.events.event_types import (  # noqa: E402
    BID_DISQUALIFIED,
    BID_SUBMITTED,
    FINAL_RANKING_CALCULATED,
    PRICE_OFFER_SEALED,
    PRICES_REVEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    REQUIREMENT_SCORED,
    TENDER_CLOSED_FOR_BIDS,
    TENDER_CREATED,
    TENDER_FAILED,
)
from app.events.schema import create_schema  # noqa: E402

_SCORES = (
    RequirementScore("req-venue", 9, 40),
    RequirementScore("req-content", 9, 30),
    RequirementScore("req-experience", 9, 30),
)


def _skip_unless_configured() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))
    if not settings.db_is_configured:
        pytest.skip(
            "SOMEE_DB_CONNECTION_STRING still holds the .env.example placeholder."
        )


def test_qualified_completion_projects_score_rows() -> None:
    """T-CMD-13. Quality 90.0. The requirement rows are the payload, not a recomputation."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    command = QualityAssessmentCompleted(bid_id, 90.0, _SCORES)

    with transaction() as conn:
        create_schema(conn)
        SqlCompleteAssessmentHandler(conn).handle(command)

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, quality_score, reason, failed_requirement_id "
            "FROM dbo.rm_bid_assessment WHERE bid_id = ?",
            bid_id,
        )
        assessment = cursor.fetchone()
        cursor.execute(
            "SELECT requirement_id, score, weight FROM dbo.rm_requirement_score "
            "WHERE bid_id = ?",
            bid_id,
        )
        scores = cursor.fetchall()
        cursor.close()

    assert len(events) == 1
    assert events[0].version == 1
    assert events[0].event_type == QUALITY_ASSESSMENT_COMPLETED
    assert events[0].event_data["quality_score"] == 90.0
    assert assessment[0] == "qualified"
    assert assessment[1] == 90.0
    assert assessment[2] is None
    assert assessment[3] is None
    assert {(row[0], row[1], row[2]) for row in scores} == {
        ("req-venue", 9, 40),
        ("req-content", 9, 30),
        ("req-experience", 9, 30),
    }


def test_completion_leaves_an_existing_score_row() -> None:
    """T-CMD-21. Venue is already 9 at weight 40. Completion adds content at 8 and 60."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"

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
        SqlScoreRequirementHandler(conn).handle(
            ScoreRequirement(
                bid_id=bid_id,
                requirement_id="req-venue",
                score=9,
                justification="The hall seats 5000.",
                sources=("https://example.com/hall",),
                rubric_version="venue-1",
            )
        )
        SqlCompleteAssessmentHandler(conn).handle(
            QualityAssessmentCompleted(
                bid_id,
                90.0,
                (
                    RequirementScore("req-venue", 9, 40),
                    RequirementScore("req-content", 8, 60),
                ),
            )
        )

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

    assert [event.event_type for event in events] == [
        BID_SUBMITTED,
        REQUIREMENT_SCORED,
        QUALITY_ASSESSMENT_COMPLETED,
    ]
    assert {(row[0], row[1], row[2]) for row in scores} == {
        ("req-venue", 9, 40),
        ("req-content", 8, 60),
    }


def test_threshold_disqualification_projects_no_requirement_rows() -> None:
    """T-CMD-14. The venue threshold failed. Quality stays null."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    command = BidDisqualified(bid_id, "threshold", failed_requirement_id="req-venue")

    with transaction() as conn:
        create_schema(conn)
        SqlCompleteAssessmentHandler(conn).handle(command)

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, quality_score, reason, failed_requirement_id "
            "FROM dbo.rm_bid_assessment WHERE bid_id = ?",
            bid_id,
        )
        assessment = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*) FROM dbo.rm_requirement_score WHERE bid_id = ?",
            bid_id,
        )
        score_count = int(cursor.fetchone()[0])
        cursor.close()

    assert len(events) == 1
    assert events[0].event_type == BID_DISQUALIFIED
    assert events[0].event_data["reason"] == "threshold"
    assert events[0].event_data["failed_requirement_id"] == "req-venue"
    assert "quality_score" not in events[0].event_data
    assert assessment[0] == "disqualified"
    assert assessment[1] is None
    assert assessment[2] == "threshold"
    assert assessment[3] == "req-venue"
    assert score_count == 0


def test_second_terminal_command_appends_nothing() -> None:
    """T-CMD-15. The first completion is version 1. The second is rejected."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    command = QualityAssessmentCompleted(bid_id, 90.0, _SCORES)

    with transaction() as conn:
        create_schema(conn)
        SqlCompleteAssessmentHandler(conn).handle(command)

    with pytest.raises(AssessmentAlreadyTerminal):
        with transaction() as conn:
            SqlCompleteAssessmentHandler(conn).handle(command)

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)

    assert len(events) == 1


def _append(conn, stream_id: str, expected_version: int, event_type: str, data: dict) -> None:
    EventStore(conn).append(stream_id, expected_version, event_type, data)


def _submit(conn, bid_id: str, tender_id: str, price_amount: int) -> None:
    _append(
        conn,
        bid_id,
        0,
        BID_SUBMITTED,
        {
            "tender_id": tender_id,
            "supplier_id": "sup-1",
            "concept": "ceremony",
            "responses": [],
        },
    )
    _append(
        conn,
        bid_id,
        1,
        PRICE_OFFER_SEALED,
        {
            "price_amount": price_amount,
            "currency": "ILS",
            "sealed_at": "2026-06-01T09:00:00Z",
        },
    )


def test_reveal_rejects_an_unassessed_bid() -> None:
    """T-CMD-16. Bid B is submitted and not terminal, so the tender stream stays put."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_a = f"bid-{uuid.uuid4()}"
    bid_b = f"bid-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)
        _append(conn, tender_id, 0, TENDER_CREATED, {"alpha": 0.6, "name": "Awards"})
        _submit(conn, bid_a, tender_id, 900_000)
        SqlCompleteAssessmentHandler(conn).handle(
            QualityAssessmentCompleted(bid_a, 82.0, _SCORES)
        )
        _submit(conn, bid_b, tender_id, 700_000)

    with pytest.raises(RevealRejected):
        with transaction() as conn:
            SqlRevealPricesHandler(conn).handle(tender_id)

    with transaction() as conn:
        events = EventStore(conn).read_stream(tender_id)

    assert [event.event_type for event in events] == [TENDER_CREATED]


def test_reveal_ranks_the_worked_example() -> None:
    """T-CMD-17. A is ₪900,000 at 82.0. B is ₪700,000 at 74.0. α = 0.6."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_a = f"bid-{uuid.uuid4()}"
    bid_b = f"bid-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)
        _append(conn, tender_id, 0, TENDER_CREATED, {"alpha": 0.6, "name": "Awards"})
        _submit(conn, bid_a, tender_id, 900_000)
        _submit(conn, bid_b, tender_id, 700_000)
        handler = SqlCompleteAssessmentHandler(conn)
        handler.handle(QualityAssessmentCompleted(bid_a, 82.0, _SCORES))
        handler.handle(QualityAssessmentCompleted(bid_b, 74.0, _SCORES))
        SqlRevealPricesHandler(conn).handle(tender_id)

    with transaction() as conn:
        events = EventStore(conn).read_stream(tender_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT bid_id, price_amount, price_score, final_score, rank "
            "FROM dbo.rm_bid_ranking WHERE tender_id = ?",
            tender_id,
        )
        rows = {row[0]: row for row in cursor.fetchall()}
        cursor.close()

    assert [event.event_type for event in events] == [
        TENDER_CREATED,
        PRICES_REVEALED,
        FINAL_RANKING_CALCULATED,
    ]
    revealed = events[1].event_data["prices"]
    amounts = {item["bid_id"]: item["amount"] for item in revealed}
    assert amounts == {bid_a: 900_000, bid_b: 700_000}
    assert rows[bid_b][4] == 1
    assert round(rows[bid_b][3], 1) == 84.4
    assert rows[bid_b][1] == 700_000
    assert rows[bid_b][2] == 100.0
    assert rows[bid_a][4] == 2
    assert round(rows[bid_a][3], 1) == 80.3
    assert rows[bid_a][1] == 900_000
    assert round(rows[bid_a][2], 1) == 77.8


def test_reveal_fails_the_tender_when_none_qualify() -> None:
    """T-CMD-18. Both bids failed a threshold. Prices stay sealed."""
    _skip_unless_configured()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_a = f"bid-{uuid.uuid4()}"
    bid_b = f"bid-{uuid.uuid4()}"

    with transaction() as conn:
        create_schema(conn)
        _append(conn, tender_id, 0, TENDER_CREATED, {"alpha": 0.6, "name": "Awards"})
        _submit(conn, bid_a, tender_id, 900_000)
        _submit(conn, bid_b, tender_id, 500_000)
        handler = SqlCompleteAssessmentHandler(conn)
        handler.handle(BidDisqualified(bid_a, "threshold", failed_requirement_id="req-venue"))
        handler.handle(BidDisqualified(bid_b, "threshold", failed_requirement_id="req-venue"))
        SqlRevealPricesHandler(conn).handle(tender_id)

    with transaction() as conn:
        events = EventStore(conn).read_stream(tender_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM dbo.rm_bid_ranking WHERE tender_id = ?",
            tender_id,
        )
        ranking_rows = int(cursor.fetchone()[0])
        cursor.close()

    assert [event.event_type for event in events] == [TENDER_CREATED, TENDER_FAILED]
    assert events[1].event_data["reason"] == "no_qualified_bids"
    assert PRICES_REVEALED not in {event.event_type for event in events}
    assert ranking_rows == 0
