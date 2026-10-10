"""Orchestrator venue slice.

Seam: `score_venue_requirement`. The real `get_tender_requirements` and
`submit_requirement_score` functions run against in-memory doubles.
`SemanticSearchService.get_rubric` runs against an in-memory store.
Web search and the model are fakes. FastMCP transport and SQL stay outside.

The model proposes 9 and reports a compliant venue only when the requirement,
the tender, the whole rubric, the regulation text, and the web page all
arrived. Otherwise it reports a breach, and the score would be 4.

T-AG-20 — the VENUE verdict is submitted; a CONTENT requirement is not.
"""

from __future__ import annotations

from app.commands.score_requirement import ScoreRequirement
from app.infrastructure.vector_store import RUBRICS, Match
from app.services.semantic_search_service import SemanticSearchService
from agent.orchestrator import score_venue_requirement
from agent.subagents.venue import (
    VenueAssessment,
    VenueMaterial,
    Verification,
    WebDocument,
)
from mcp_server.server import get_tender_requirements, submit_requirement_score

_ANSWER = "היכל התרבות, 8000 מושבים, שני פתחי יציאה ותיאום משטרה."
_DESCRIPTION = "אולם של 2000 מושבים לפחות"
_TENDER_NAME = "טקס פרסי התרבות"
_REGION = "תל אביב"
_EVENT_DATE = "2026-06-01"
_RUBRIC_TEXT = (
    "requirement_type: VENUE\n"
    'rubric_version: "3"\n'
    "### 9–10\n"
    "Official capacity verified and the plan meets every retrieved obligation.\n"
    "### 0\n"
    "The capacity claim is false.\n"
)
_RUBRIC_VERSION = "3"
_REGULATION = "מעל 500 איש נדרשים לפחות שני פתחי יציאה נפרדים."
_PAGE_URL = "https://venue.example/official"
_PAGE_TEXT = "קיבולת רשמית: 8000 מושבים."
_JUSTIFICATION = "Official capacity is 8000 and the plan meets the retrieved rules."


class _Requirements:
    def __init__(self, views: dict[str, dict]) -> None:
        self._views = views

    def fetch(self, tender_id: str) -> dict | None:
        return self._views.get(tender_id)


class _Scores:
    def __init__(self) -> None:
        self.commands: list[ScoreRequirement] = []

    def handle(self, command: ScoreRequirement) -> dict:
        self.commands.append(command)
        return {"accepted": True, "event_version": len(self.commands)}


class _RubricStore:
    """Key lookup only. A semantic query is not how the rubric is loaded."""

    def get(self, collection: str, ids: list[str]) -> list[Match]:
        if collection == RUBRICS and "rubric:VENUE" in ids:
            return [
                Match(
                    id="rubric:VENUE",
                    document=_RUBRIC_TEXT,
                    metadata={
                        "requirement_type": "VENUE",
                        "rubric_version": _RUBRIC_VERSION,
                    },
                    distance=0.0,
                )
            ]
        return []

    def query(self, collection: str, text: str, k: int, where: dict | None = None) -> list[Match]:
        raise AssertionError("the rubric is loaded by id")

    def upsert(self, collection: str, ids: list[str], documents: list[str], metadata: list[dict]) -> None:
        raise AssertionError("the test store is read-only")


class _Regulations:
    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]:
        return [
            {
                "id": "reg-exits",
                "text": _REGULATION,
                "metadata": {"source": "mass_events_safety"},
                "score": 1.0,
            }
        ]


class _Web:
    def search(self, query: str) -> tuple[WebDocument, ...]:
        return (
            WebDocument(url=_PAGE_URL, title="Official capacity", content=_PAGE_TEXT),
        )


class _Model:
    def assess(self, material: VenueMaterial) -> VenueAssessment:
        pages = "\n".join(document.content for document in material.web_results)
        regulations = "\n".join(material.regulations)
        if (
            material.answer == _ANSWER
            and material.requirement.id == "req-venue"
            and material.requirement.description == _DESCRIPTION
            and material.rubric_text == _RUBRIC_TEXT
            and material.tender.name == _TENDER_NAME
            and material.tender.event_date == _EVENT_DATE
            and material.tender.region == _REGION
            and _REGULATION in regulations
            and _PAGE_TEXT in pages
        ):
            return VenueAssessment(
                proposed_score=9,
                justification=_JUSTIFICATION,
                sources=(_PAGE_URL,),
                verifications=(
                    Verification(claim=_JUSTIFICATION, status="verified", source=_PAGE_URL),
                ),
                capacity_claim="verified",
                meets_tender_capacity=True,
                regulation_breached=False,
            )
        return VenueAssessment(
            proposed_score=9,
            justification="A retrieved obligation is breached.",
            sources=(_PAGE_URL,),
            verifications=(),
            capacity_claim="verified",
            meets_tender_capacity=True,
            regulation_breached=True,
        )


def test_score_venue_requirement_submits_only_the_venue_score() -> None:
    """T-AG-20. CONTENT stays unscored. The submitted version is the rubric's."""
    query = _Requirements(
        {
            "tender-1": {
                "tender": {
                    "name": _TENDER_NAME,
                    "type": "culture",
                    "event_date": _EVENT_DATE,
                    "region": _REGION,
                    "alpha": 0.6,
                    "price": 900000,
                },
                "requirements": [
                    {
                        "id": "req-venue",
                        "type": "VENUE",
                        "weight": 40,
                        "is_threshold": True,
                        "description": _DESCRIPTION,
                        "price": 900000,
                    },
                    {
                        "id": "req-content",
                        "type": "CONTENT",
                        "weight": 20,
                        "is_threshold": False,
                        "description": "מופע מוזיקלי",
                        "price": 900000,
                    },
                ],
            }
        }
    )
    scores = _Scores()

    verdict = score_venue_requirement(
        "bid-1",
        "tender-1",
        _ANSWER,
        requirements=lambda tender_id: get_tender_requirements(tender_id, query),
        rubrics=SemanticSearchService(_RubricStore()),
        search=_Regulations(),
        web=_Web(),
        model=_Model(),
        submit=lambda bid_id, requirement_id, score, justification, sources, rubric_version: (
            submit_requirement_score(
                bid_id,
                requirement_id,
                score,
                justification,
                sources,
                rubric_version,
                scores,
            )
        ),
    )

    assert verdict.score == 9
    assert scores.commands == [
        ScoreRequirement(
            bid_id="bid-1",
            requirement_id="req-venue",
            score=9,
            justification=_JUSTIFICATION,
            sources=(_PAGE_URL,),
            rubric_version=_RUBRIC_VERSION,
            scored_by="agent",
        )
    ]
