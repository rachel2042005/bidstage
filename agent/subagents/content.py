"""CONTENT requirement evaluator (`05-agent-spec.md` §5).

The orchestrator owns `get_tender_requirements`, the whole rubric, the
threshold short-circuit, and `submit_requirement_score`. `evaluate_content`
scores one content requirement and returns the §6 verdict. Tender context is
`name`, `event_date`, and `region`. This function has no price argument.

`search` is satisfied by `SemanticSearchService.search_bids`, called with no
`tender_id`. `web` is the search port in front of Tavily. `model` proposes a
0–10 integer and three findings. `evaluate_content` then applies the rubric's
first-match rule for the bands this slice pins (`T-AG-21`, `T-AG-22`, `T-AG-23`):

1. `performer_claim == "disproved"` → score 0.
2. `both_components_sound` and `performer_claim == "verified"` → the proposal
   is pinned to 9–10.
3. `substantive_defect` → the proposal is pinned to 3–4.

Quality, price, and rank stay in `app/domain/scoring.py` (`NFR-DET-1`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

PerformerClaim = Literal["verified", "disproved", "inconclusive"]


@dataclass(frozen=True)
class ContentRequirement:
    """One CONTENT requirement. Threshold handling belongs to the orchestrator."""

    id: str
    description: str


@dataclass(frozen=True)
class TenderContext:
    """Event facts a content check may cite. Price is absent (`NFR-AGT-4`)."""

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
    status: PerformerClaim
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
class ContentMaterial:
    """Everything the model sees. `rubric_text` is the whole rubric."""

    requirement: ContentRequirement
    answer: str
    rubric_text: str
    tender: TenderContext
    concepts: tuple[str, ...]
    web_results: tuple[WebDocument, ...]


@dataclass(frozen=True)
class ContentAssessment:
    """The model's proposal. The first-match rule may replace `proposed_score`."""

    proposed_score: int
    justification: str
    sources: tuple[str, ...]
    verifications: tuple[Verification, ...]
    performer_claim: PerformerClaim
    both_components_sound: bool
    substantive_defect: bool


class ConceptSearch(Protocol):
    """The `search_bids` method of `SemanticSearchService`."""

    def search_bids(
        self, query: str, tender_id: str | None = None, top_k: int = 5
    ) -> list[dict]: ...


class WebSearch(Protocol):
    """Port for performer and work lookups."""

    def search(self, query: str) -> tuple[WebDocument, ...]: ...


class ContentModel(Protocol):
    """Structured-output model. Callers set `temperature = 0`."""

    def assess(self, material: ContentMaterial) -> ContentAssessment: ...


def evaluate_content(
    requirement: ContentRequirement,
    answer: str,
    rubric: Rubric,
    tender: TenderContext,
    search: ConceptSearch,
    web: WebSearch,
    model: ContentModel,
) -> RequirementVerdict:
    """Score one CONTENT requirement (`T-AG-21`, `T-AG-22`, `T-AG-23`)."""
    concepts = _concept_texts(search.search_bids(_concept_query(requirement, answer, tender)))
    pages = web.search(_web_query(requirement, answer, tender))
    assessment = model.assess(
        ContentMaterial(
            requirement=requirement,
            answer=answer,
            rubric_text=rubric.text,
            tender=tender,
            concepts=concepts,
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


def _concept_query(
    requirement: ContentRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        ("CONTENT", tender.name, tender.region, tender.event_date, requirement.description, answer)
    )


def _web_query(
    requirement: ContentRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        (
            "performer work active real",
            "CONTENT",
            tender.name,
            tender.region,
            tender.event_date,
            requirement.description,
            answer,
        )
    )


def _concept_texts(hits: list[dict]) -> tuple[str, ...]:
    return tuple(
        text
        for hit in hits
        if isinstance(text := hit.get("text"), str) and text.strip()
    )


def _apply(assessment: ContentAssessment) -> int:
    """First matching rubric rule. The proposal is pinned into that band."""
    proposed = _proposed_score(assessment.proposed_score)
    if assessment.performer_claim == "disproved":
        return 0
    if assessment.both_components_sound and assessment.performer_claim == "verified":
        return _pin(proposed, 9, 10)
    if assessment.substantive_defect:
        return _pin(proposed, 3, 4)
    raise ValueError(
        "No CONTENT band is pinned for this finding combination "
        f"({assessment.performer_claim!r}, "
        f"both_components_sound={assessment.both_components_sound}, "
        f"substantive_defect={assessment.substantive_defect})."
    )


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
