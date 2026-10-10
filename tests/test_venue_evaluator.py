"""VENUE requirement evaluator.

Seam: `evaluate_venue`, with fakes for `SemanticSearchService`, the web-search
port, and the model. MCP and deepagents stay outside this file.

The model proposes 9. It reports the constraining finding only when the
material contains the whole rubric, the regulation text, and the web page.
A verdict that echoes 9 fails every band below.

T-AG-13 — a breached retrieved regulation caps the score at 0–4.
T-AG-16 — verified capacity below the tender figure, with no breach, scores 3 or 4.
T-AG-17 — a disproved capacity claim scores 0.
"""

from __future__ import annotations

from agent.subagents.venue import (
    CapacityClaim,
    RequirementVerdict,
    Rubric,
    TenderContext,
    VenueAssessment,
    VenueMaterial,
    VenueRequirement,
    Verification,
    WebDocument,
    evaluate_venue,
)

_REQUIREMENT = VenueRequirement(
    id="req-venue",
    description="אולם של 2000 מושבים לפחות",
)
_TENDER = TenderContext(
    name="טקס פרסי התרבות",
    event_date="2026-06-01",
    region="תל אביב",
)
_RUBRIC = Rubric(
    text=(
        "requirement_type: VENUE\n"
        'rubric_version: "1"\n'
        "### 9–10\n"
        "Official capacity verified and the plan meets every retrieved obligation.\n"
        "### 3–4\n"
        "Official capacity is below the tender figure and the plan complies.\n"
        "### 0–4\n"
        "A retrieved police, fire, licence, or accessibility obligation is breached.\n"
        "### 0\n"
        "The capacity claim is false.\n"
    ),
    version="1",
)
_PAGE = "https://venue.example/official"


class _Regulations:
    """Stand-in for SemanticSearchService.search_regulations."""

    def __init__(self, text: str) -> None:
        self._text = text

    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]:
        return [
            {
                "id": "reg-exits",
                "text": self._text,
                "metadata": {"source": "mass_events_safety", "section": "כבאות"},
                "score": 1.0,
            }
        ]


class _Web:
    def __init__(self, content: str) -> None:
        self._content = content

    def search(self, query: str) -> tuple[WebDocument, ...]:
        return (WebDocument(url=_PAGE, title="Official capacity", content=self._content),)


class _Model:
    """Reports `found` only when the whole rubric and both evidence texts arrive."""

    def __init__(
        self,
        regulation_marker: str,
        page_marker: str,
        found: VenueAssessment,
    ) -> None:
        self._regulation_marker = regulation_marker
        self._page_marker = page_marker
        self._found = found

    def assess(self, material: VenueMaterial) -> VenueAssessment:
        regulations = "\n".join(material.regulations)
        pages = "\n".join(document.content for document in material.web_results)
        if (
            material.rubric_text == _RUBRIC.text
            and self._regulation_marker in regulations
            and self._page_marker in pages
        ):
            return self._found
        return VenueAssessment(
            proposed_score=9,
            justification="The venue meets the requirement.",
            sources=(_PAGE,),
            verifications=(),
            capacity_claim="verified",
            meets_tender_capacity=True,
            regulation_breached=False,
        )


def _found(
    *,
    capacity_claim: CapacityClaim,
    meets_tender_capacity: bool,
    regulation_breached: bool,
    claim: str,
) -> VenueAssessment:
    status: CapacityClaim = "disproved" if capacity_claim == "disproved" else "verified"
    return VenueAssessment(
        proposed_score=9,
        justification=claim,
        sources=(_PAGE,),
        verifications=(Verification(claim=claim, status=status, source=_PAGE),),
        capacity_claim=capacity_claim,
        meets_tender_capacity=meets_tender_capacity,
        regulation_breached=regulation_breached,
    )


def _evaluate(
    *,
    answer: str,
    regulation: str,
    page: str,
    found: VenueAssessment,
) -> RequirementVerdict:
    return evaluate_venue(
        _REQUIREMENT,
        answer,
        _RUBRIC,
        _TENDER,
        _Regulations(regulation),
        _Web(page),
        _Model(regulation, page, found),
    )


def test_breached_regulation_caps_the_score_at_four() -> None:
    """T-AG-13. One exit where the retrieved rule requires two. Model proposes 9."""
    regulation = "מעל 500 איש נדרשים לפחות שני פתחי יציאה נפרדים."
    page = "קיבולת רשמית: 8000 מושבים."
    verdict = _evaluate(
        answer="היכל התרבות, 8000 מושבים, פתח יציאה אחד.",
        regulation=regulation,
        page=page,
        found=_found(
            capacity_claim="verified",
            meets_tender_capacity=True,
            regulation_breached=True,
            claim="The plan provides one exit where the retrieved rule requires two.",
        ),
    )

    assert verdict.requirement_id == "req-venue"
    assert type(verdict.score) is int
    assert 0 <= verdict.score <= 4


def test_undersized_compliant_venue_scores_three_or_four() -> None:
    """T-AG-16. Official capacity 1500 against a 2000-seat tender. Model proposes 9."""
    regulation = "התוכנית מקיימת את חובות המשטרה, הכבאות, הרישוי והנגישות."
    page = "קיבולת רשמית: 1500 מושבים."
    verdict = _evaluate(
        answer="אולם הקאמרי, קיבולת מוצהרת 1500, שני פתחי יציאה ותיאום משטרה.",
        regulation=regulation,
        page=page,
        found=_found(
            capacity_claim="verified",
            meets_tender_capacity=False,
            regulation_breached=False,
            claim="Official capacity is 1500, below the tender figure of 2000.",
        ),
    )

    assert verdict.requirement_id == "req-venue"
    assert type(verdict.score) is int
    assert verdict.score in (3, 4)


def test_disproved_capacity_claim_scores_zero() -> None:
    """T-AG-17. The supplier claims 5000; the official page says 1200. Model proposes 9."""
    regulation = "לא נמצאה הפרה של חובה שנשלפה."
    page = "הקיבולת הרשמית היא 1200 מושבים."
    verdict = _evaluate(
        answer="היכל התרבות מכיל 5000 מושבים.",
        regulation=regulation,
        page=page,
        found=_found(
            capacity_claim="disproved",
            meets_tender_capacity=False,
            regulation_breached=False,
            claim="The supplier claims 5000 seats; the official page says 1200.",
        ),
    )

    assert verdict.requirement_id == "req-venue"
    assert type(verdict.score) is int
    assert verdict.score == 0
