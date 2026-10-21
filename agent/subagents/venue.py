"""VENUE requirement evaluator (`05-agent-spec.md` §5).

The orchestrator owns `get_tender_requirements`, the whole rubric, the
threshold short-circuit, and `submit_requirement_score`. `evaluate_venue`
scores one venue requirement and returns the §6 verdict. Tender context is
`name`, `event_date`, and `region`. This function has no price argument.

`search` is satisfied by `SemanticSearchService`. `web` is the search port
in front of Tavily. `model` proposes a 0–10 integer and three findings.
`evaluate_venue` then applies the rubric's first-match rule:

1. `capacity_claim == "disproved"` → score 0.
2. `regulation_breached` → the proposal is pinned to 0–4.
3. Verified capacity with `meets_tender_capacity` false and no breach → 3 or 4.
4. `capacity_claim == "inconclusive"` → the proposal is pinned to 5–6.
5. Verified capacity that meets the tender, with no breach → the proposal is pinned to 7–10.

Quality, price, and rank stay in `app/domain/scoring.py` (`NFR-DET-1`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

CapacityClaim = Literal["verified", "disproved", "inconclusive"]


@dataclass(frozen=True)
class VenueRequirement:
    """One VENUE requirement. Threshold handling belongs to the orchestrator."""

    id: str
    description: str


@dataclass(frozen=True)
class TenderContext:
    """Event facts a venue check may cite. Price is absent (`NFR-AGT-4`)."""

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
    status: CapacityClaim
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
class VenueMaterial:
    """Everything the model sees. `rubric_text` is the whole rubric."""

    requirement: VenueRequirement
    answer: str
    rubric_text: str
    tender: TenderContext
    regulations: tuple[str, ...]
    web_results: tuple[WebDocument, ...]


@dataclass(frozen=True)
class VenueAssessment:
    """The model's proposal. The first-match rule may replace `proposed_score`."""

    proposed_score: int
    justification: str
    sources: tuple[str, ...]
    verifications: tuple[Verification, ...]
    capacity_claim: CapacityClaim
    meets_tender_capacity: bool
    regulation_breached: bool


class RegulationSearch(Protocol):
    """The `search_regulations` method of `SemanticSearchService`."""

    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]: ...


class WebSearch(Protocol):
    """Port for official-capacity and licensing lookups."""

    def search(self, query: str) -> tuple[WebDocument, ...]: ...


class VenueModel(Protocol):
    """Structured-output model. Callers set `temperature = 0`."""

    def assess(self, material: VenueMaterial) -> VenueAssessment: ...


def evaluate_venue(
    requirement: VenueRequirement,
    answer: str,
    rubric: Rubric,
    tender: TenderContext,
    search: RegulationSearch,
    web: WebSearch,
    model: VenueModel,
) -> RequirementVerdict:
    """Score one VENUE requirement (`T-AG-13`, `T-AG-16`, `T-AG-17`)."""
    regulations = _regulation_texts(
        search.search_regulations(_regulation_query(requirement, answer, tender))
    )
    pages = web.search(_web_query(requirement, answer, tender))
    assessment = model.assess(
        VenueMaterial(
            requirement=requirement,
            answer=answer,
            rubric_text=rubric.text,
            tender=tender,
            regulations=regulations,
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


def _regulation_query(
    requirement: VenueRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        ("VENUE", tender.name, tender.region, tender.event_date, requirement.description, answer)
    )


def _web_query(
    requirement: VenueRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        (
            "official capacity licensing accessibility",
            "VENUE",
            tender.name,
            tender.region,
            tender.event_date,
            requirement.description,
            answer,
        )
    )


def _regulation_texts(hits: list[dict]) -> tuple[str, ...]:
    return tuple(
        text
        for hit in hits
        if isinstance(text := hit.get("text"), str) and text.strip()
    )


def _apply(assessment: VenueAssessment) -> int:
    """First matching rubric rule. The proposal is pinned into that band."""
    proposed = _proposed_score(assessment.proposed_score)
    claim = assessment.capacity_claim
    if claim == "disproved":
        return 0
    if assessment.regulation_breached:
        return _pin(proposed, 0, 4)
    if claim == "verified" and not assessment.meets_tender_capacity:
        return _pin(proposed, 3, 4)
    if claim == "inconclusive":
        return _pin(proposed, 5, 6)
    if claim == "verified":
        return _pin(proposed, 7, 10)
    raise ValueError(f"Unknown capacity claim {claim!r}.")


def _proposed_score(score: int) -> int:
    if type(score) is not int or not 0 <= score <= 10:
        raise ValueError(
            f"proposed score must be an integer from 0 to 10, got {score!r}."
        )
    return score


def _pin(score: int, low: int, high: int) -> int:
    if score < low:
        return low
    if score > high:
        return high
    return score
