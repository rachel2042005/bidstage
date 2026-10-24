"""Phase 4 smoke path: queue, runner, SQL scores, and projections.

Seam: `build_runtime` and `run_once`, the single-batch entry point.
Skips when SQL Server is not configured. Stream ids are fresh. Nothing is deleted.

One pending row is claimed. Venue and content are scored through the SQL
score handler. Completion appends `QualityAssessmentCompleted`. The queue row
becomes `completed`. `rm_bid_assessment.status` is `qualified` and the quality
score is 86.0. The chat model, the vector store, and web search are stood in
so this test makes no network call.
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

from agent.runner import build_runtime, run_once  # noqa: E402
from agent.subagents.content import ContentAssessment, ContentMaterial  # noqa: E402
from agent.subagents.venue import VenueAssessment, VenueMaterial  # noqa: E402
from app.config import ConfigError, load_settings  # noqa: E402
from app.events import EventStore  # noqa: E402
from app.events.db import transaction  # noqa: E402
from app.events.event_types import (  # noqa: E402
    BID_SUBMITTED,
    PRICE_OFFER_SEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    TENDER_CLOSED_FOR_BIDS,
    TENDER_CREATED,
)
from app.events.schema import create_schema  # noqa: E402
from app.infrastructure.vector_store import RUBRICS, Match  # noqa: E402
from app.projections.bid_for_assessment import enqueue_bid_for_assessment  # noqa: E402

_VENUE_ID = "req-venue"
_CONTENT_ID = "req-content"
_VENUE_WEIGHT = 40
_CONTENT_WEIGHT = 60
_VENUE_SCORE = 8
_CONTENT_SCORE = 9
# (40 × 8 + 60 × 9) / 10. Literal from the scoring model, not recomputed here.
_QUALITY = 86.0
_PRICE_AMOUNT = 900000


def _skip_unless_configured() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))
    if not settings.db_is_configured:
        pytest.skip(
            "SOMEE_DB_CONNECTION_STRING still holds the .env.example placeholder."
        )


def _make_oldest_pending(conn, bid_id: str) -> None:
    """Claim takes the oldest queued row. This fixture has to be that row."""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.rm_bid_for_assessment
        SET queued_at = DATEADD(day, -1, (
            SELECT MIN(queued_at) FROM dbo.rm_bid_for_assessment
        ))
        WHERE bid_id = ?
        """,
        bid_id,
    )
    cursor.close()


class _OfflineStore:
    """Rubric lookup without Chroma or an embedding call."""

    def __init__(self, settings: object) -> None:
        del settings

    def get(self, collection: str, ids: list[str]) -> list[Match]:
        if collection != RUBRICS:
            return []
        found = []
        for doc_id in ids:
            if doc_id == "rubric:VENUE":
                found.append(
                    Match(
                        id=doc_id,
                        document="VENUE rubric\n### 7–10\nCapacity verified.\n",
                        metadata={"rubric_version": "venue-1"},
                        distance=0.0,
                    )
                )
            elif doc_id == "rubric:CONTENT":
                found.append(
                    Match(
                        id=doc_id,
                        document="CONTENT rubric\n### 9–10\nSchedule and concept.\n",
                        metadata={"rubric_version": "content-1"},
                        distance=0.0,
                    )
                )
        return found

    def query(
        self,
        collection: str,
        text: str,
        k: int,
        where: dict | None = None,
    ) -> list[Match]:
        del collection, text, k, where
        return []


class _OfflineAssessor:
    """Fixed findings in the bands that produce scores 8 and 9."""

    def assess(
        self, material: VenueMaterial | ContentMaterial
    ) -> VenueAssessment | ContentAssessment:
        if isinstance(material, VenueMaterial):
            return VenueAssessment(
                proposed_score=_VENUE_SCORE,
                justification="Official capacity meets the tender.",
                sources=("https://example.com/hall",),
                verifications=(),
                capacity_claim="verified",
                meets_tender_capacity=True,
                regulation_breached=False,
            )
        if isinstance(material, ContentMaterial):
            return ContentAssessment(
                proposed_score=_CONTENT_SCORE,
                justification="The schedule and the named performer both hold.",
                sources=("https://example.com/program",),
                verifications=(),
                performer_claim="verified",
                both_components_sound=True,
                substantive_defect=False,
            )
        raise AssertionError(f"unexpected material {type(material).__name__}")


def test_pending_bid_completes_quality_assessment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One pending bid becomes a qualified assessment at quality 86.0."""
    _skip_unless_configured()
    settings = load_settings()
    tender_id = f"tender-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"
    supplier_id = f"sup-{uuid.uuid4()}"
    bidder_name = f"אורורה-{uuid.uuid4()}"
    host_name = f"רינה-{uuid.uuid4()}"
    responses = [
        {"requirement_id": _VENUE_ID, "answer": "היכל התרבות, 5000 מושבים"},
        {"requirement_id": _CONTENT_ID, "answer": "19:00 פתיחה ומופע מרכזי"},
    ]

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
            "2026-11-01",
            "Tel Aviv",
            0.6,
        )
        cursor.execute(
            "INSERT INTO dbo.rm_requirement "
            "(tender_id, requirement_id, position, type, weight, is_threshold, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            tender_id,
            _VENUE_ID,
            1,
            "VENUE",
            _VENUE_WEIGHT,
            0,
            "Hall for 5000",
        )
        cursor.execute(
            "INSERT INTO dbo.rm_requirement "
            "(tender_id, requirement_id, position, type, weight, is_threshold, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            tender_id,
            _CONTENT_ID,
            2,
            "CONTENT",
            _CONTENT_WEIGHT,
            0,
            "Evening program",
        )
        cursor.execute("INSERT INTO dbo.approved_hosts (name) VALUES (?)", host_name)
        cursor.close()
        store = EventStore(conn)
        store.append(
            tender_id,
            0,
            TENDER_CREATED,
            {
                "name": "Summer Stage",
                "event_type": "festival",
                "event_date": "2026-11-01",
                "region": "Tel Aviv",
                "estimated_budget": 1000000,
                "deadline": "2026-12-01T00:00:00Z",
                "alpha": 0.6,
                "organizer_id": "org-1",
            },
        )
        store.append(
            tender_id,
            1,
            TENDER_CLOSED_FOR_BIDS,
            {"closed_at": "2026-10-02T00:00:00Z", "bid_count": 1},
        )
        store.append(
            bid_id,
            0,
            BID_SUBMITTED,
            {
                "tender_id": tender_id,
                "supplier_id": supplier_id,
                "bidder_name": bidder_name,
                "concept": "טקס פתיחה",
                "responses": responses,
            },
        )
        store.append(
            bid_id,
            1,
            PRICE_OFFER_SEALED,
            {
                "price_amount": _PRICE_AMOUNT,
                "currency": "ILS",
                "sealed_at": "2026-10-01T09:00:00Z",
            },
        )
        enqueue_bid_for_assessment(
            conn,
            bid_id,
            tender_id,
            supplier_id,
            "טקס פתיחה",
            responses,
        )
        _make_oldest_pending(conn, bid_id)

    monkeypatch.setattr(
        "app.infrastructure.vector_store.ChromaVectorStore",
        _OfflineStore,
    )
    monkeypatch.setattr(
        "agent.adapters.assessor.build_assessor",
        lambda settings: _OfflineAssessor(),
    )
    monkeypatch.setattr(
        "agent.adapters.tavily.TavilyPageSearch.search",
        lambda self, query: (),
    )

    runtime = build_runtime(settings)
    try:
        processed = run_once(
            runtime.queue,
            requirements=runtime.requirements,
            rubrics=runtime.rubrics,
            search=runtime.search,
            web=runtime.web,
            venue_model=runtime.venue_model,
            content_model=runtime.content_model,
            experience_model=runtime.experience_model,
            host_model=runtime.host_model,
            music_model=runtime.music_model,
            submit=runtime.submit,
            complete=runtime.complete,
        )
    finally:
        runtime.queue._conn.close()

    with transaction() as conn:
        events = EventStore(conn).read_stream(bid_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM dbo.rm_bid_for_assessment WHERE bid_id = ?",
            bid_id,
        )
        queue_status = cursor.fetchone()[0]
        cursor.execute(
            "SELECT status, quality_score FROM dbo.rm_bid_assessment WHERE bid_id = ?",
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

    assert processed == 1
    assert queue_status == "completed"
    assert assessment is not None
    assert assessment[0] == "qualified"
    assert float(assessment[1]) == _QUALITY
    assert {(row[0], row[1], row[2]) for row in scores} == {
        (_VENUE_ID, _VENUE_SCORE, _VENUE_WEIGHT),
        (_CONTENT_ID, _CONTENT_SCORE, _CONTENT_WEIGHT),
    }
    completed = [
        event for event in events if event.event_type == QUALITY_ASSESSMENT_COMPLETED
    ]
    assert len(completed) == 1
    assert completed[0].event_data["quality_score"] == _QUALITY
