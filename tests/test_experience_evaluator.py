"""EXPERIENCE requirement evaluator.

Seam: `evaluate_experience`, with fakes for the portfolio port, the web-search
port, and the model. MCP, deepagents, and SemanticSearchService stay outside
this file.

The model proposes outside the band under test. It reports the constraining
finding only when the material contains the whole rubric, this supplier's
portfolio text, and the web page. `search_portfolios` yields that text only
when `supplier_id` matches.

T-AG-24 — three outdoor productions verified, none disproved; proposal 4
pins to 9–10.
T-AG-25 — outdoor events claimed, none verified and none disproved; proposal 9
pins to 5–6.
T-AG-26 — one production disproved beside three verified outdoor shows;
proposal 9 scores 0.
"""

from __future__ import annotations

from agent.subagents.experience import (
    ExperienceAssessment,
    ExperienceMaterial,
    ExperienceRequirement,
    RequirementVerdict,
    Rubric,
    TenderContext,
    Verification,
    WebDocument,
    evaluate_experience,
)

_SUPPLIER = "sup-aurora"
_REQUIREMENT = ExperienceRequirement(
    id="req-experience",
    description="רקורד בהפקת אירועי חוץ או אצטדיון",
)
_TENDER = TenderContext(
    name="פסטיבל קיץ בפארק הירקון",
    event_date="2026-07-15",
    region="תל אביב",
)
_RUBRIC = Rubric(
    text=(
        "requirement_type: EXPERIENCE\n"
        'rubric_version: "1"\n'
        "### 9–10\n"
        "Three or more outdoor or stadium productions were independently verified, and none were disproved.\n"
        "### 5–6\n"
        "Outdoor or stadium events were claimed, and none were verified or disproved.\n"
        "### 0\n"
        "One disproved production scores 0 even when others were verified.\n"
    ),
    version="1",
)
_PAGE = "https://press.example/festivals"


class _Portfolios:
    """Stand-in for search_portfolios. Another supplier's id yields nothing."""

    def __init__(self, text: str, supplier_id: str) -> None:
        self._text = text
        self._supplier_id = supplier_id

    def search_portfolios(
        self, query: str, supplier_id: str, top_k: int = 5
    ) -> list[dict]:
        if supplier_id != self._supplier_id:
            return []
        return [
            {
                "id": "portfolio-aurora",
                "text": self._text,
                "metadata": {"supplier_id": supplier_id},
                "score": 1.0,
            }
        ]


class _Web:
    def __init__(self, content: str) -> None:
        self._content = content

    def search(self, query: str) -> tuple[WebDocument, ...]:
        return (
            WebDocument(url=_PAGE, title="Festival report", content=self._content),
        )


class _Model:
    """Reports `found` only when the whole rubric and both evidence texts arrive."""

    def __init__(
        self,
        portfolio_marker: str,
        page_marker: str,
        found: ExperienceAssessment,
    ) -> None:
        self._portfolio_marker = portfolio_marker
        self._page_marker = page_marker
        self._found = found

    def assess(self, material: ExperienceMaterial) -> ExperienceAssessment:
        portfolios = "\n".join(material.portfolios)
        pages = "\n".join(document.content for document in material.web_results)
        if (
            material.rubric_text == _RUBRIC.text
            and self._portfolio_marker in portfolios
            and self._page_marker in pages
        ):
            return self._found
        return ExperienceAssessment(
            proposed_score=4,
            justification="The track record meets the requirement.",
            sources=(),
            verifications=(),
            any_disproved=False,
            verified_outdoor_count=0,
            outdoor_claimed=False,
        )


def _found(
    *,
    any_disproved: bool,
    verified_outdoor_count: int,
    outdoor_claimed: bool,
    claim: str,
    status: str,
    proposed_score: int = 9,
) -> ExperienceAssessment:
    return ExperienceAssessment(
        proposed_score=proposed_score,
        justification=claim,
        sources=(_PAGE,),
        verifications=(
            Verification(claim=claim, status=status, source=_PAGE),
        ),
        any_disproved=any_disproved,
        verified_outdoor_count=verified_outdoor_count,
        outdoor_claimed=outdoor_claimed,
    )


def _evaluate(
    *,
    answer: str,
    portfolio: str,
    page: str,
    found: ExperienceAssessment,
) -> RequirementVerdict:
    return evaluate_experience(
        _REQUIREMENT,
        answer,
        _RUBRIC,
        _TENDER,
        _SUPPLIER,
        _Portfolios(portfolio, _SUPPLIER),
        _Web(page),
        _Model(portfolio, page, found),
    )


def test_three_verified_outdoor_productions_score_nine_or_ten() -> None:
    """T-AG-24. Three stadium shows corroborated. Model proposes 4."""
    portfolio = "אורורה הפיקה את בלומפילד, את פארק הירקון ואת חוצות תל אביב."
    page = "שלושת אירועי החוץ אומתו בכתבות נפרדות."
    verdict = _evaluate(
        answer="הפקנו את בלומפילד 2023, פארק הירקון 2024 וחוצות תל אביב 2025.",
        portfolio=portfolio,
        page=page,
        found=_found(
            any_disproved=False,
            verified_outdoor_count=3,
            outdoor_claimed=True,
            proposed_score=4,
            status="verified",
            claim="Three outdoor productions were independently verified.",
        ),
    )

    assert verdict.requirement_id == "req-experience"
    assert type(verdict.score) is int
    assert verdict.score in (9, 10)


def test_unverified_outdoor_claim_scores_five_or_six() -> None:
    """T-AG-25. An outdoor festival was claimed and no source confirms or denies it. Model proposes 9."""
    portfolio = "אורורה מציינת פסטיבל חוצות בלי תאריך ובלי מקום."
    page = "לא נמצא דיווח על הפסטיבל, ולא נמצאה הכחשה."
    verdict = _evaluate(
        answer="הפקנו פסטיבל חוצות בעיר, בלי שם מקום ובלי תאריך.",
        portfolio=portfolio,
        page=page,
        found=_found(
            any_disproved=False,
            verified_outdoor_count=0,
            outdoor_claimed=True,
            status="inconclusive",
            claim="An outdoor festival was claimed and neither verified nor disproved.",
        ),
    )

    assert verdict.requirement_id == "req-experience"
    assert type(verdict.score) is int
    assert verdict.score in (5, 6)


def test_one_disproved_production_scores_zero() -> None:
    """T-AG-26. Three stadium shows check out; a fourth was someone else's. Model proposes 9."""
    portfolio = "אורורה הפיקה שלושה אירועי אצטדיון. את פסטיבל הנמל הפיק מפיק אחר."
    page = "פסטיבל הנמל יוחס בטעות לאורורה; שלושת האצטדיונים אומתו."
    verdict = _evaluate(
        answer="הפקנו את בלומפילד, פארק הירקון, חוצות תל אביב, ואת פסטיבל הנמל.",
        portfolio=portfolio,
        page=page,
        found=_found(
            any_disproved=True,
            verified_outdoor_count=3,
            outdoor_claimed=True,
            status="disproved",
            claim="The harbor festival was produced by someone else.",
        ),
    )

    assert verdict.requirement_id == "req-experience"
    assert type(verdict.score) is int
    assert verdict.score == 0
