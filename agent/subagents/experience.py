"""EXPERIENCE requirement evaluator (`05-agent-spec.md` §5).

The orchestrator owns `get_tender_requirements`, the whole rubric, the
threshold short-circuit, and `submit_requirement_score`. `evaluate_experience`
scores one experience requirement and returns the §6 verdict. Tender context
is `name`, `event_date`, and `region`. `supplier_id` scopes portfolio search.
This function has no price argument.

`search` is the portfolio port: `search_portfolios(query, supplier_id)`.
`SemanticSearchService` does not implement that method yet (`02` §6). `web`
is the search port in front of Tavily. `model` proposes a 0–10 integer and
three findings. `evaluate_experience` then applies the rubric's first-match
rule for the bands this slice pins (`T-AG-24`, `T-AG-25`, `T-AG-26`):

1. `any_disproved` → score 0.
2. `verified_outdoor_count >= 3` → the proposal is pinned to 9–10.
3. `outdoor_claimed` and `verified_outdoor_count == 0` → the proposal is pinned to 5–6.

Quality, price, and rank stay in `app/domain/scoring.py` (`NFR-DET-1`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ClaimStatus = Literal["verified", "disproved", "inconclusive"]


@dataclass(frozen=True)
class ExperienceRequirement:
    """One EXPERIENCE requirement. Threshold handling belongs to the orchestrator."""

    id: str
    description: str


@dataclass(frozen=True)
class TenderContext:
    """Event facts an experience check may cite. Price is absent (`NFR-AGT-4`)."""

    name: str
    event_date: str
    region: str


@dataclass(frozen=True)
class Rubric:
    """The unchunked rubric document. `version` is the frontmatter value."""

    text: str
    version: str


@dataclass(frozen=True)
class WebDocument:
    """One page returned by the web-search port."""

    url: str
    title: str
    content: str


@dataclass(frozen=True)
class Verification:
    """One entry of the §6 `verifications` array."""

    claim: str
    status: ClaimStatus
    source: str


@dataclass(frozen=True)
class RequirementVerdict:
    """The §6 verdict. `score` is an integer 0–10."""

    requirement_id: str
    score: int
    justification: str
    sources: tuple[str, ...]
    verifications: tuple[Verification, ...]


@dataclass(frozen=True)
class ExperienceMaterial:
    """Everything the model sees. `rubric_text` is the whole rubric."""

    requirement: ExperienceRequirement
    answer: str
    rubric_text: str
    tender: TenderContext
    supplier_id: str
    portfolios: tuple[str, ...]
    web_results: tuple[WebDocument, ...]


@dataclass(frozen=True)
class ExperienceAssessment:
    """The model's proposal. The first-match rule may replace `proposed_score`."""

    proposed_score: int
    justification: str
    sources: tuple[str, ...]
    verifications: tuple[Verification, ...]
    any_disproved: bool
    verified_outdoor_count: int
    outdoor_claimed: bool


class PortfolioSearch(Protocol):
    """Portfolio lookup scoped to one supplier. Not on SemanticSearchService yet."""

    def search_portfolios(
        self, query: str, supplier_id: str, top_k: int = 5
    ) -> list[dict]: ...


class WebSearch(Protocol):
    """Port for reviews and reports of claimed productions."""

    def search(self, query: str) -> tuple[WebDocument, ...]: ...


class ExperienceModel(Protocol):
    """Structured-output model. Callers set `temperature = 0`."""

    def assess(self, material: ExperienceMaterial) -> ExperienceAssessment: ...


def evaluate_experience(
    requirement: ExperienceRequirement,
    answer: str,
    rubric: Rubric,
    tender: TenderContext,
    supplier_id: str,
    search: PortfolioSearch,
    web: WebSearch,
    model: ExperienceModel,
) -> RequirementVerdict:
    """Score one EXPERIENCE requirement (`T-AG-24`, `T-AG-25`, `T-AG-26`)."""
    portfolios = _portfolio_texts(
        search.search_portfolios(
            _portfolio_query(requirement, answer, tender), supplier_id
        )
    )
    pages = web.search(_web_query(requirement, answer, tender))
    assessment = model.assess(
        ExperienceMaterial(
            requirement=requirement,
            answer=answer,
            rubric_text=rubric.text,
            tender=tender,
            supplier_id=supplier_id,
            portfolios=portfolios,
            web_results=pages,
        )
    )
    return RequirementVerdict(
        requirement_id=requirement.id,
        score=_apply(assessment),
        justification=assessment.justification,
        sources=assessment.sources,
        verifications=assessment.verifications,
    )


def _portfolio_query(
    requirement: ExperienceRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        (
            "EXPERIENCE",
            tender.name,
            tender.region,
            tender.event_date,
            requirement.description,
            answer,
        )
    )


def _web_query(
    requirement: ExperienceRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        (
            "outdoor stadium production review report",
            "EXPERIENCE",
            tender.name,
            tender.region,
            tender.event_date,
            requirement.description,
            answer,
        )
    )


def _portfolio_texts(hits: list[dict]) -> tuple[str, ...]:
    return tuple(
        text
        for hit in hits
        if isinstance(text := hit.get("text"), str) and text.strip()
    )


def _apply(assessment: ExperienceAssessment) -> int:
    """First matching rubric rule. The proposal is pinned into that band."""
    proposed = _proposed_score(assessment.proposed_score)
    count = _verified_count(assessment.verified_outdoor_count)
    if assessment.any_disproved:
        return 0
    if count >= 3:
        return _pin(proposed, 9, 10)
    if assessment.outdoor_claimed and count == 0:
        return _pin(proposed, 5, 6)
    raise ValueError(
        "No EXPERIENCE band is pinned for this finding combination "
        f"(any_disproved={assessment.any_disproved}, "
        f"verified_outdoor_count={count}, "
        f"outdoor_claimed={assessment.outdoor_claimed})."
    )


def _proposed_score(score: int) -> int:
    if type(score) is not int or not 0 <= score <= 10:
        raise ValueError(
            f"proposed score must be an integer from 0 to 10, got {score!r}."
        )
    return score


def _verified_count(count: int) -> int:
    if type(count) is not int or count < 0:
        raise ValueError(
            f"verified outdoor count must be an integer >= 0, got {count!r}."
        )
    return count


def _pin(score: int, low: int, high: int) -> int:
    if score < low:
        return low
    if score > high:
        return high
    return score
