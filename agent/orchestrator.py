"""Venue slice of the orchestrator (`05-agent-spec.md` §4, steps 2–5).

`score_venue_requirement` loads the tender through `get_tender_requirements`,
loads the unchunked VENUE rubric, calls `evaluate_venue`, and submits the
verdict through `submit_requirement_score`. The supplier's answer is an
argument: reading `rm_bid_for_assessment` arrives with that projection.
Other requirement types, the threshold short-circuit, and the terminal
event are outside this function.
"""

from __future__ import annotations

from typing import Any, Protocol

from agent.subagents.venue import (
    RegulationSearch,
    RequirementVerdict,
    Rubric,
    TenderContext,
    VenueModel,
    VenueRequirement,
    WebSearch,
    evaluate_venue,
)


class RequirementsTool(Protocol):
    """`get_tender_requirements(tender_id)` as the MCP tool exposes it."""

    def __call__(self, tender_id: str) -> dict[str, Any]: ...


class RubricIndex(Protocol):
    """`SemanticSearchService.get_rubric`."""

    def get_rubric(self, requirement_type: str) -> dict | None: ...


class ScoreTool(Protocol):
    """`submit_requirement_score` as the MCP tool exposes it."""

    def __call__(
        self,
        bid_id: str,
        requirement_id: str,
        score: int,
        justification: str,
        sources: list[str],
        rubric_version: str,
    ) -> dict[str, Any]: ...


def score_venue_requirement(
    bid_id: str,
    tender_id: str,
    answer: str,
    *,
    requirements: RequirementsTool,
    rubrics: RubricIndex,
    search: RegulationSearch,
    web: WebSearch,
    model: VenueModel,
    submit: ScoreTool,
) -> RequirementVerdict:
    """Score the tender's one VENUE requirement and submit it (`T-AG-20`)."""
    payload = requirements(tender_id)
    venue = _one_venue(payload["requirements"])
    stored = rubrics.get_rubric("VENUE")
    if stored is None:
        raise LookupError("VENUE rubric is not seeded.")
    version = _rubric_version(stored)
    tender = payload["tender"]
    verdict = evaluate_venue(
        VenueRequirement(id=venue["id"], description=venue["description"]),
        answer,
        Rubric(text=stored["text"], version=version),
        TenderContext(
            name=tender["name"],
            event_date=str(tender["event_date"]),
            region=tender["region"],
        ),
        search,
        web,
        model,
    )
    submit(
        bid_id,
        verdict.requirement_id,
        verdict.score,
        verdict.justification,
        list(verdict.sources),
        version,
    )
    return verdict


def _one_venue(requirements: list[dict]) -> dict:
    venues = [item for item in requirements if item.get("type") == "VENUE"]
    if len(venues) != 1:
        raise ValueError(
            f"This step scores exactly one VENUE requirement, found {len(venues)}."
        )
    return venues[0]


def _rubric_version(stored: dict) -> str:
    version = stored.get("metadata", {}).get("rubric_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("VENUE rubric_version is required.")
    return version
