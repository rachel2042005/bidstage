"""CONTENT requirement evaluator.

Seam: `evaluate_content`, with fakes for `SemanticSearchService`, the web-search
port, and the model. MCP and deepagents stay outside this file.

The model proposes outside the band under test. It reports the constraining
finding only when the material contains the whole rubric, the precedent
concept, and the web page. `search_bids` yields that concept only when
`tender_id` is omitted.

T-AG-21 — both components sound and the central names verified; proposal 4
pins to 9–10.
T-AG-22 — a self-contradictory schedule; proposal 9 pins to 3–4.
T-AG-23 — a disproved performer; proposal 9 scores 0, ahead of a sound program.
"""

from __future__ import annotations

from agent.subagents.content import (
    ContentAssessment,
    ContentMaterial,
    ContentRequirement,
    PerformerClaim,
    RequirementVerdict,
    Rubric,
    TenderContext,
    Verification,
    WebDocument,
    evaluate_content,
)

_REQUIREMENT = ContentRequirement(
    id="req-content",
    description="לוח תוכנית וקונספט לטקס פרסי התרבות",
)
_TENDER = TenderContext(
    name="טקס פרסי התרבות",
    event_date="2026-06-01",
    region="תל אביב",
)
_RUBRIC = Rubric(
    text=(
        "requirement_type: CONTENT\n"
        'rubric_version: "1"\n'
        "### 9–10\n"
        "Schedule and concept are both present and on-theme, and the central names are independently verified.\n"
        "### 3–4\n"
        "A present component is substantively defective, such as a self-contradictory schedule.\n"
        "### 0\n"
        "A named performer, work, or segment is fabricated.\n"
    ),
    version="1",
)
_PAGE = "https://performers.example/official"


class _Concepts:
    """Stand-in for SemanticSearchService.search_bids. A tender filter yields nothing."""

    def __init__(self, text: str) -> None:
        self._text = text

    def search_bids(
        self, query: str, tender_id: str | None = None, top_k: int = 5
    ) -> list[dict]:
        if tender_id is not None:
            return []
        return [
            {
                "id": "concept-prior",
                "text": self._text,
                "metadata": {},
                "score": 1.0,
            }
        ]


class _Web:
    def __init__(self, content: str) -> None:
        self._content = content

    def search(self, query: str) -> tuple[WebDocument, ...]:
        return (
            WebDocument(url=_PAGE, title="Performer record", content=self._content),
        )


class _Model:
    """Reports `found` only when the whole rubric and both evidence texts arrive."""

    def __init__(
        self,
        concept_marker: str,
        page_marker: str,
        found: ContentAssessment,
    ) -> None:
        self._concept_marker = concept_marker
        self._page_marker = page_marker
        self._found = found

    def assess(self, material: ContentMaterial) -> ContentAssessment:
        concepts = "\n".join(material.concepts)
        pages = "\n".join(document.content for document in material.web_results)
        if (
            material.rubric_text == _RUBRIC.text
            and self._concept_marker in concepts
            and self._page_marker in pages
        ):
            return self._found
        return ContentAssessment(
            proposed_score=5,
            justification="The program meets the requirement.",
            sources=(),
            verifications=(),
            performer_claim="inconclusive",
            both_components_sound=False,
            substantive_defect=False,
        )


def _found(
    *,
    performer_claim: PerformerClaim,
    both_components_sound: bool,
    substantive_defect: bool,
    claim: str,
    proposed_score: int = 9,
) -> ContentAssessment:
    return ContentAssessment(
        proposed_score=proposed_score,
        justification=claim,
        sources=(_PAGE,),
        verifications=(
            Verification(claim=claim, status=performer_claim, source=_PAGE),
        ),
        performer_claim=performer_claim,
        both_components_sound=both_components_sound,
        substantive_defect=substantive_defect,
    )


def _evaluate(
    *,
    answer: str,
    concept: str,
    page: str,
    found: ContentAssessment,
) -> RequirementVerdict:
    return evaluate_content(
        _REQUIREMENT,
        answer,
        _RUBRIC,
        _TENDER,
        _Concepts(concept),
        _Web(page),
        _Model(concept, page, found),
    )


def test_verified_concept_scores_nine_or_ten() -> None:
    """T-AG-21. Full schedule and on-theme concept; names verified. Model proposes 4."""
    concept = "הצעה זוכה קודמת: לוח מלא וקונספט של מחווה ליוצרים ישראליים."
    page = "ריטה פעילה, והיצירה המרכזית קיימת."
    verdict = _evaluate(
        answer="19:00 פתיחה, 20:00 מחווה ליוצרים ישראליים עם ריטה, 21:30 סיום.",
        concept=concept,
        page=page,
        found=_found(
            performer_claim="verified",
            both_components_sound=True,
            substantive_defect=False,
            proposed_score=4,
            claim="The schedule and concept are on-theme, and Rita is independently verified.",
        ),
    )

    assert verdict.requirement_id == "req-content"
    assert type(verdict.score) is int
    assert verdict.score in (9, 10)


def test_contradictory_schedule_scores_three_or_four() -> None:
    """T-AG-22. Relevant concept, schedule that ends before it opens. Model proposes 9."""
    concept = "הצעה זוכה קודמת: לוח רציף בלי חפיפה, קונספט של מחווה."
    page = "המבצעים המרכזיים פעילים."
    verdict = _evaluate(
        answer="קונספט: מחווה ליוצרים ישראליים. לוח: סיום ב-19:00, פתיחה ב-21:00.",
        concept=concept,
        page=page,
        found=_found(
            performer_claim="verified",
            both_components_sound=False,
            substantive_defect=True,
            claim="The concept is on-theme and the schedule ends before it opens.",
        ),
    )

    assert verdict.requirement_id == "req-content"
    assert type(verdict.score) is int
    assert verdict.score in (3, 4)


def test_disproved_performer_scores_zero() -> None:
    """T-AG-23. The named act does not exist. Model proposes 9 and calls the program sound."""
    concept = "אין תקדים למבצע בשם הזה במושגים הקודמים."
    page = "אין אמן או יצירה בשם זמרי הירח הסגולים."
    verdict = _evaluate(
        answer="19:00 פתיחה, 20:00 הופעת זמרי הירח הסגולים, 21:30 סיום. קונספט: מחווה ליוצרים.",
        concept=concept,
        page=page,
        found=_found(
            performer_claim="disproved",
            both_components_sound=True,
            substantive_defect=False,
            claim="Purple Moon Singers is not a real act.",
        ),
    )

    assert verdict.requirement_id == "req-content"
    assert type(verdict.score) is int
    assert verdict.score == 0
