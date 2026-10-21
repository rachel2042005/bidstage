"""MUSIC requirement evaluator (`05-agent-spec.md` §5).

The orchestrator owns `get_tender_requirements`, the whole rubric, the
threshold short-circuit, and `submit_requirement_score`. `evaluate_music`
scores one music requirement and returns the §6 verdict. Tender context is
`name`, `event_date`, and `region`. This function has no price argument.

`search` is satisfied by `SemanticSearchService.search_bids`, called with no
`tender_id`. `web` is the search port in front of Tavily. `model` proposes a
0–10 integer and four findings. `evaluate_music` then applies the rubric's
first-match rule for the bands this slice pins (`T-AG-32`, `T-AG-33`, `T-AG-34`):

1. `act_claim == "disproved"` → score 0.
2. `tender_requires_live` and not `live_offered` → the proposal is pinned to 3–4.
3. `act_claim == "verified"` and `rider_complete` and (`live_offered` or not
   `tender_requires_live`) → the proposal is pinned to 9–10.

Quality, price, and rank stay in `app/domain/scoring.py` (`NFR-DET-1`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ActClaim = Literal["verified", "disproved", "inconclusive"]


@dataclass(frozen=True)
class MusicRequirement:
    """One MUSIC requirement. Threshold handling belongs to the orchestrator."""

    id: str
    description: str


@dataclass(frozen=True)
class TenderContext:
    """Event facts a music check may cite. Price is absent (`NFR-AGT-4`)."""

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
    status: ActClaim
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
class MusicMaterial:
    """Everything the model sees. `rubric_text` is the whole rubric."""

    requirement: MusicRequirement
    answer: str
    rubric_text: str
    tender: TenderContext
    concepts: tuple[str, ...]
    web_results: tuple[WebDocument, ...]


@dataclass(frozen=True)
class MusicAssessment:
    """The model's proposal. The first-match rule may replace `proposed_score`."""

    proposed_score: int
    justification: str
    sources: tuple[str, ...]
    verifications: tuple[Verification, ...]
    act_claim: ActClaim
    live_offered: bool
    tender_requires_live: bool
    rider_complete: bool


class ConceptSearch(Protocol):
    """The `search_bids` method of `SemanticSearchService`."""

    def search_bids(
        self, query: str, tender_id: str | None = None, top_k: int = 5
    ) -> list[dict]: ...


class WebSearch(Protocol):
    """Port for act lookups."""

    def search(self, query: str) -> tuple[WebDocument, ...]: ...


class MusicModel(Protocol):
    """Structured-output model. Callers set `temperature = 0`."""

    def assess(self, material: MusicMaterial) -> MusicAssessment: ...


def evaluate_music(
    requirement: MusicRequirement,
    answer: str,
    rubric: Rubric,
    tender: TenderContext,
    search: ConceptSearch,
    web: WebSearch,
    model: MusicModel,
) -> RequirementVerdict:
    """Score one MUSIC requirement (`T-AG-32`, `T-AG-33`, `T-AG-34`)."""
    concepts = _concept_texts(search.search_bids(_concept_query(requirement, answer, tender)))
    pages = web.search(_web_query(requirement, answer, tender))
    assessment = model.assess(
        MusicMaterial(
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
    requirement: MusicRequirement, answer: str, tender: TenderContext
) -> str:
    return "\n".join(
        ("MUSIC", tender.name, tender.region, tender.event_date, requirement.description, answer)
    )


def _web_query(requirement: MusicRequirement, answer: str, tender: TenderContext) -> str:
    return "\n".join(
        (
            "act touring active",
            "MUSIC",
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


def _apply(assessment: MusicAssessment) -> int:
    """First matching rubric rule. The proposal is pinned into that band."""
    proposed = _proposed_score(assessment.proposed_score)
    if assessment.act_claim == "disproved":
        return 0
    if assessment.tender_requires_live and not assessment.live_offered:
        return _pin(proposed, 3, 4)
    if (
        assessment.act_claim == "verified"
        and assessment.rider_complete
        and (assessment.live_offered or not assessment.tender_requires_live)
    ):
        return _pin(proposed, 9, 10)
    raise ValueError(
        "No MUSIC band is pinned for this finding combination "
        f"({assessment.act_claim!r}, live_offered={assessment.live_offered}, "
        f"tender_requires_live={assessment.tender_requires_live}, "
        f"rider_complete={assessment.rider_complete})."
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
