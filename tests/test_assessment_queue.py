"""Assessment queue projection and claim.

Seams: `create_schema`, later `enqueue_bid_for_assessment` and
`SqlAssessmentQueue.claim`. Skips when SQL Server is not configured.
Fresh ids. Nothing is deleted.

T-AG-1 — rm_bid_for_assessment has the locked columns and no price column.
T-AG-39 — one pending bid is claimed once, with its venue answer intact.
T-AG-40 — a sealed price on the bid stream stays out of the queue and the claim.
T-AG-11 — two connections claim one pending bid; one receives it.
T-CMD-19 — completion marks the queued bid completed, so claim returns nothing.
"""

from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import bootstrap

bootstrap.init()

import pytest  # noqa: E402

from agent.runner import AssessmentWork  # noqa: E402
from app.commands.complete_assessment import (  # noqa: E402
    QualityAssessmentCompleted,
    RequirementScore,
    SqlCompleteAssessmentHandler,
)
from app.config import ConfigError, load_settings  # noqa: E402
from app.events import EventStore  # noqa: E402
from app.events.db import connect, transaction  # noqa: E402
from app.events.event_types import BID_SUBMITTED, PRICE_OFFER_SEALED  # noqa: E402
from app.events.schema import create_schema  # noqa: E402
from app.projections.bid_for_assessment import (  # noqa: E402
    SqlAssessmentQueue,
    enqueue_bid_for_assessment,
)

_COLUMNS = {
    "bid_id",
    "tender_id",
    "supplier_id",
    "concept",
    "responses_json",
    "status",
    "queued_at",
    "claimed_at",
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


def test_assessment_queue_schema_has_no_price_column() -> None:
    """T-AG-1. The agent's table lists the locked columns and nothing priced."""
    _skip_unless_configured()
    with transaction() as conn:
        create_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT name
            FROM sys.columns
            WHERE object_id = OBJECT_ID('dbo.rm_bid_for_assessment')
            """
        )
        names = {row[0] for row in cursor.fetchall()}
        cursor.close()

    assert names == _COLUMNS
    assert all("price" not in name.casefold() for name in names)


def test_claim_returns_the_venue_answer_and_the_next_claim_is_empty() -> None:
    """T-AG-39. The venue answer is היכל. A second claim finds nothing pending."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    tender_id = f"tender-{uuid.uuid4()}"
    conn = connect()
    try:
        create_schema(conn)
        conn.commit()
        enqueue_bid_for_assessment(
            conn,
            bid_id,
            tender_id,
            "sup-1",
            "טקס פתיחה",
            [{"requirement_id": "req-venue", "answer": "היכל"}],
        )
        work = SqlAssessmentQueue(conn).claim()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT status, claimed_at
            FROM dbo.rm_bid_for_assessment
            WHERE bid_id = ?
            """,
            bid_id,
        )
        status, claimed_at = cursor.fetchone()
        cursor.close()
        second = SqlAssessmentQueue(conn).claim()
    finally:
        conn.close()

    assert work == AssessmentWork(
        bid_id=bid_id,
        tender_id=tender_id,
        supplier_id="sup-1",
        answers={"req-venue": "היכל"},
    )
    assert status == "claimed"
    assert claimed_at is not None
    assert second is None


def test_claim_leaves_out_the_sealed_price() -> None:
    """T-AG-40. 900000 sits on the bid stream and nowhere on the claimed work."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    tender_id = f"tender-{uuid.uuid4()}"
    conn = connect()
    try:
        create_schema(conn)
        store = EventStore(conn)
        store.append(
            bid_id,
            0,
            BID_SUBMITTED,
            {
                "tender_id": tender_id,
                "supplier_id": "sup-1",
                "concept": "טקס",
                "responses": [{"requirement_id": "req-venue", "answer": "אולם"}],
            },
        )
        store.append(
            bid_id,
            1,
            PRICE_OFFER_SEALED,
            {
                "price_amount": 900000,
                "currency": "ILS",
                "sealed_at": "2026-06-01T09:00:00Z",
            },
        )
        enqueue_bid_for_assessment(
            conn,
            bid_id,
            tender_id,
            "sup-1",
            "טקס",
            [{"requirement_id": "req-venue", "answer": "אולם"}],
        )
        work = SqlAssessmentQueue(conn).claim()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM dbo.rm_bid_for_assessment WHERE bid_id = ?",
            bid_id,
        )
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    assert work is not None
    assert work.answers == {"req-venue": "אולם"}
    rendered = " ".join("" if value is None else str(value) for value in row)
    assert "900000" not in rendered
    assert "900000" not in work.bid_id
    assert "900000" not in work.tender_id
    assert "900000" not in work.supplier_id
    assert all("900000" not in answer for answer in work.answers.values())


def test_two_connections_claim_one_bid_once() -> None:
    """T-AG-11. One pending bid. One claim wins it; the other receives nothing."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    tender_id = f"tender-{uuid.uuid4()}"
    setup = connect()
    try:
        create_schema(setup)
        setup.commit()
        enqueue_bid_for_assessment(
            setup,
            bid_id,
            tender_id,
            "sup-1",
            "טקס",
            [{"requirement_id": "req-venue", "answer": "אולם"}],
        )
        setup.commit()
    finally:
        setup.close()

    results: list[AssessmentWork | None] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def claim_once() -> None:
        conn = connect()
        try:
            barrier.wait(timeout=10)
            results.append(SqlAssessmentQueue(conn).claim())
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    workers = [threading.Thread(target=claim_once) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert errors == []
    assert [work.bid_id if work is not None else None for work in results].count(bid_id) == 1
    assert results.count(None) == 1


def test_completion_closes_the_queued_bid() -> None:
    """T-CMD-19. After quality completion the row is completed and claim is empty."""
    _skip_unless_configured()
    bid_id = f"bid-{uuid.uuid4()}"
    tender_id = f"tender-{uuid.uuid4()}"
    with transaction() as conn:
        create_schema(conn)
        enqueue_bid_for_assessment(
            conn,
            bid_id,
            tender_id,
            "sup-1",
            "טקס",
            [{"requirement_id": "req-venue", "answer": "אולם"}],
        )
        SqlCompleteAssessmentHandler(conn).handle(
            QualityAssessmentCompleted(
                bid_id,
                90.0,
                (RequirementScore("req-venue", 9, 40),),
            )
        )

    conn = connect()
    try:
        work = SqlAssessmentQueue(conn).claim()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM dbo.rm_bid_for_assessment WHERE bid_id = ?",
            bid_id,
        )
        status = cursor.fetchone()[0]
        cursor.close()
    finally:
        conn.close()

    assert status == "completed"
    assert work is None
