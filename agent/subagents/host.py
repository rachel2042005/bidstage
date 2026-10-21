"""HOST requirement evaluator (`05-agent-spec.md` §5).

The orchestrator owns `get_tender_requirements`, the whole rubric, the
threshold short-circuit, and `submit_requirement_score`. `evaluate_host`
scores one host requirement and returns the §6 verdict. Tender context is
`name`, `event_date`, and `region`. This function has no price argument.

`approved_hosts` is the seeded list from that payload. Membership is
`strip().casefold()` on both sides. `web` is the search port in front of
Tavily. `model` proposes a 0–10 integer and four findings. `evaluate_host`
then applies the rubric's first-match rule for the bands this slice pins
(`T-AG-12`, `T-AG-30`, `T-AG-31`):

1. No host name after `strip` → score 0.
2. A named host who is not on the list → the proposal is pinned to 1–2.
3. The primary host is listed and `primary_conflict` → pinned to 3–4.
4. The primary and backup are both listed and `backup_conflict` is false →
   pinned to 9–10.

Quality, price, and rank stay in `app/domain/scoring.py` (`NFR-DET-1`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

ClaimStatus = Literal["verified", "disproved", "inconclusive"]


@dataclass(frozen=True)
class HostRequirement:
    """One HOST requirement. Threshold handling belongs to the orchestrator."""

    id: str
    description: str


@dataclass(frozen=True)
class TenderContext:
    """Event facts a host check may cite. Price is absent (`NFR-AGT-4`)."""

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
class HostMaterial:
    """Everything the model sees. `rubric_text` is the whole rubric."""

    requirement: HostRequirement
    answer: str
    rubric_text: str
    tender: TenderContext
    approved_hosts: tuple[str, ...]
    web_results: tuple[WebDocument, ...]


@dataclass(frozen=True)
class HostAssessment:
    """The model's proposal. The first-match rule may replace `proposed_score`."""

    proposed_score: int
    justification: str
    sources: tuple[str, ...]
    verifications: tuple[Verification, ...]
    host_name: str
    backup_name: str
    primary_conflict: bool
    backup_conflict: bool


class WebSearch(Protocol):
    """Port for same-date engagement lookups."""

    def search(self, query: str) -> tuple[WebDocument, ...]: ...


class HostModel(Protocol):
    """Structured-output model. Callers set `temperature = 0`."""

    def assess(self, material: HostMaterial) -> HostAssessment: ...


def evaluate_host(
    requirement: HostRequirement,
    answer: str,
    rubric: Rubric,
    tender: TenderContext,
    approved_hosts: Sequence[str],
    web: WebSearch,
    model: HostModel,
) -> RequirementVerdict:
    """Score one HOST requirement (`T-AG-12`, `T-AG-30`, `T-AG-31`)."""
    hosts = tuple(approved_hosts)
    pages = web.search(_web_query(requirement, answer, tender))
    assessment = model.assess(
        HostMaterial(
            requirement=requirement,
            answer=answer,
            rubric_text=rubric.text,
            tender=tender,
            approved_hosts=hosts,
            web_results=pages,
        )
    )
    return RequirementVerdict(
        requirement_id=requirement.id,
        score=_apply(assessment, hosts),
        justification=assessment.justification,
        sources=assessment.sources,
        verifications=assessment.verifications,
    )


def _web_query(requirement: HostRequirement, answer: str, tender: TenderContext) -> str:
    return "\n".join(
        (
            "host engagement conflict",
            "HOST",
            tender.name,
            tender.region,
            tender.event_date,
            requirement.description,
            answer,
        )
    )


def _apply(assessment: HostAssessment, approved_hosts: Sequence[str]) -> int:
    """First matching rubric rule. The proposal is pinned into that band."""
    proposed = _proposed_score(assessment.proposed_score)
    if not isinstance(assessment.host_name, str) or not assessment.host_name.strip():
        return 0
    if not _listed(assessment.host_name, approved_hosts):
        return _pin(proposed, 1, 2)
    if assessment.primary_conflict:
        return _pin(proposed, 3, 4)
    if (
        isinstance(assessment.backup_name, str)
        and _listed(assessment.backup_name, approved_hosts)
        and not assessment.backup_conflict
    ):
        return _pin(proposed, 9, 10)
    raise ValueError(
        "No HOST band is pinned for this finding combination "
        f"(host_name={assessment.host_name!r}, backup_name={assessment.backup_name!r}, "
        f"primary_conflict={assessment.primary_conflict}, "
        f"backup_conflict={assessment.backup_conflict})."
    )


def _listed(name: str, approved_hosts: Sequence[str]) -> bool:
    key = name.strip().casefold()
    return any(host.strip().casefold() == key for host in approved_hosts)


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
