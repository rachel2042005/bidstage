"""MUSIC requirement evaluator.

Seam: `evaluate_music`, with fakes for `SemanticSearchService`, the web-search
port, and the model. MCP and deepagents stay outside this file.

The model proposes outside the band under test. It reports the constraining
finding only when the material contains the whole rubric, the precedent
concept, and the web page. `search_bids` yields that concept only when
`tender_id` is omitted.

T-AG-32 — a live performance, a complete rider, and verified acts; proposal 4
pins to 9–10.
T-AG-33 — the tender requires a live performance and the bid has none;
proposal 9 pins to 3–4.
T-AG-34 — a disproved act beside a live program and a complete rider;
proposal 9 scores 0.
"""

from __future__ import annotations

from agent.subagents.music import (
    ActClaim,
    MusicAssessment,
    MusicMaterial,
    MusicRequirement,
    RequirementVerdict,
    Rubric,
    TenderContext,
    Verification,
    WebDocument,
    evaluate_music,
)

_REQUIREMENT = MusicRequirement(
    id="req-music",
    description="הופעה חיה וריידר טכני לבמה, הגברה וחשמל",
)
_TENDER = TenderContext(
    name="טקס פרסי התרבות",
    event_date="2026-06-01",
    region="תל אביב",
)
_RUBRIC = Rubric(
    text=(
        "requirement_type: MUSIC\n"
        'rubric_version: "1"\n'
        "### 9–10\n"
        "The live performance is detailed, the central acts are verified active, "
        "and the rider covers stage, amplification, and power.\n"
        "### 3–4\n"
        "The tender requires a live performance and the bid does not include one.\n"
        "### 0\n"
        "A named act is fabricated.\n"
    ),
    version="1",
)
_PAGE = "https://acts.example/official"


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
        return (WebDocument(url=_PAGE, title="Act record", content=self._content),)


class _Model:
    """Reports `found` only when the whole rubric and both evidence texts arrive."""

    def __init__(
        self,
        concept_marker: str,
        page_marker: str,
        found: MusicAssessment,
    ) -> None:
        self._concept_marker = concept_marker
        self._page_marker = page_marker
        self._found = found

    def assess(self, material: MusicMaterial) -> MusicAssessment:
        concepts = "\n".join(material.concepts)
        pages = "\n".join(document.content for document in material.web_results)
        if (
            material.rubric_text == _RUBRIC.text
            and self._concept_marker in concepts
            and self._page_marker in pages
        ):
            return self._found
        return MusicAssessment(
            proposed_score=5,
            justification="The music plan was not assessed.",
            sources=(),
            verifications=(),
            act_claim="inconclusive",
            live_offered=False,
            tender_requires_live=False,
            rider_complete=False,
        )


def _found(
    *,
    act_claim: ActClaim,
    live_offered: bool,
    tender_requires_live: bool,
    rider_complete: bool,
    claim: str,
    proposed_score: int = 9,
) -> MusicAssessment:
    return MusicAssessment(
        proposed_score=proposed_score,
        justification=claim,
        sources=(_PAGE,),
        verifications=(Verification(claim=claim, status=act_claim, source=_PAGE),),
        act_claim=act_claim,
        live_offered=live_offered,
        tender_requires_live=tender_requires_live,
        rider_complete=rider_complete,
    )


def _evaluate(
    *,
    answer: str,
    concept: str,
    page: str,
    found: MusicAssessment,
) -> RequirementVerdict:
    return evaluate_music(
        _REQUIREMENT,
        answer,
        _RUBRIC,
        _TENDER,
        _Concepts(concept),
        _Web(page),
        _Model(concept, page, found),
    )


def test_verified_live_performance_scores_nine_or_ten() -> None:
    """T-AG-32. Live set, complete rider, acts verified active. Model proposes 4."""
    concept = "הצעה זוכה קודמת: הופעה חיה עם ריידר לבמה, הגברה וחשמל."
    page = "עידן רייכל פעיל ומסייר."
    verdict = _evaluate(
        answer="הופעה חיה של עידן רייכל. ריידר: במה, הגברה וחשמל.",
        concept=concept,
        page=page,
        found=_found(
            act_claim="verified",
            live_offered=True,
            tender_requires_live=True,
            rider_complete=True,
            proposed_score=4,
            claim="Idan Raichel is active, the set is live, and the rider covers stage, sound, and power.",
        ),
    )

    assert verdict.requirement_id == "req-music"
    assert type(verdict.score) is int
    assert verdict.score in (9, 10)


def test_missing_live_performance_scores_three_or_four() -> None:
    """T-AG-33. The tender asks for a live set and the bid offers a recording."""
    concept = "הצעות זוכות קודמות כללו הופעה חיה על הבמה."
    page = "האמנים פעילים, וההצעה מתארת מוזיקה מוקלטת."
    verdict = _evaluate(
        answer="מוזיקה מוקלטת בלבד, בלי הופעה חיה. ריידר: במה, הגברה וחשמל.",
        concept=concept,
        page=page,
        found=_found(
            act_claim="verified",
            live_offered=False,
            tender_requires_live=True,
            rider_complete=True,
            claim="The tender requires a live performance and the bid offers a recording.",
        ),
    )

    assert verdict.requirement_id == "req-music"
    assert type(verdict.score) is int
    assert verdict.score in (3, 4)


def test_disproved_act_scores_zero() -> None:
    """T-AG-34. The live set and rider are complete. The named act is fabricated."""
    concept = "אין תקדים להרכב בשם הזה במושגים הקודמים."
    page = "אין אמן או הרכב בשם זמרי הירח הסגולים."
    verdict = _evaluate(
        answer="הופעה חיה של זמרי הירח הסגולים. ריידר: במה, הגברה וחשמל.",
        concept=concept,
        page=page,
        found=_found(
            act_claim="disproved",
            live_offered=True,
            tender_requires_live=True,
            rider_complete=True,
            claim="Purple Moon Singers is not a real act.",
        ),
    )

    assert verdict.requirement_id == "req-music"
    assert type(verdict.score) is int
    assert verdict.score == 0
