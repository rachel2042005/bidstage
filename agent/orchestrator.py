"""Dispatch for the implemented requirement types (`05-agent-spec.md` §4).

`score_bid` loads the tender through `get_tender_requirements`, loads each
rubric whole, calls the matching evaluator, and submits the verdict through
`submit_requirement_score`. Answers are a map from requirement id to response
text. `supplier_id` scopes the experience portfolio search. `approved_hosts`
is passed only to `evaluate_host`. A type outside the closed five raises
`ValueError` before any score is submitted. A `HOST` requirement with no
`approved_hosts` on the payload does too. A threshold requirement scoring
below 6 is submitted, and later requirements are not dispatched (`T-AG-8`).

Claiming, quality, and the terminal event stay in the runner.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent.subagents.content import (
    ContentModel,
    ContentRequirement,
    Rubric as ContentRubric,
    TenderContext as ContentTender,
    evaluate_content,
)
from agent.subagents.host import (
    HostModel,
    HostRequirement,
    Rubric as HostRubric,
    TenderContext as HostTender,
    evaluate_host,
)
from agent.subagents.music import (
    MusicModel,
    MusicRequirement,
    Rubric as MusicRubric,
    TenderContext as MusicTender,
    evaluate_music,
)
from agent.subagents.experience import (
    ExperienceModel,
    ExperienceRequirement,
    Rubric as ExperienceRubric,
    TenderContext as ExperienceTender,
    evaluate_experience,
)
from agent.subagents.venue import (
    Rubric as VenueRubric,
    TenderContext as VenueTender,
    VenueModel,
    VenueRequirement,
    evaluate_venue,
)

_IMPLEMENTED = frozenset({"VENUE", "CONTENT", "HOST", "MUSIC", "EXPERIENCE"})


class RequirementsTool(Protocol):
    """`get_tender_requirements(tender_id)` as the MCP tool exposes it."""

    def __call__(self, tender_id: str) -> dict[str, Any]: ...


class RubricIndex(Protocol):
    """`SemanticSearchService.get_rubric`."""

    def get_rubric(self, requirement_type: str) -> dict | None: ...


class EvidenceSearch(Protocol):
    """Regulation, concept, and portfolio lookups the three evaluators use."""

    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]: ...

    def search_bids(
        self, query: str, tender_id: str | None = None, top_k: int = 5
    ) -> list[dict]: ...

    def search_portfolios(
        self, query: str, supplier_id: str, top_k: int = 5
    ) -> list[dict]: ...


class PageSearch(Protocol):
    """Shared web-search port. Each evaluator reads `url`, `title`, and `content`."""

    def search(self, query: str) -> tuple[Any, ...]: ...


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


def score_bid(
    bid_id: str,
    tender_id: str,
    supplier_id: str,
    answers: Mapping[str, str],
    *,
    requirements: RequirementsTool,
    rubrics: RubricIndex,
    search: EvidenceSearch,
    web: PageSearch,
    venue_model: VenueModel,
    content_model: ContentModel,
    experience_model: ExperienceModel,
    host_model: HostModel,
    music_model: MusicModel,
    submit: ScoreTool,
) -> None:
    """Score the five requirement types in list order (`T-AG-20`, `T-AG-35`, `T-AG-8`)."""
    payload = requirements(tender_id)
    items = payload["requirements"]
    _require_implemented(items)
    hosts = _approved_hosts(items, payload)
    tender = _tender_fields(payload["tender"])
    for item in items:
        kind = item["type"]
        stored = rubrics.get_rubric(kind)
        if stored is None:
            raise LookupError(f"{kind} rubric is not seeded.")
        version = _rubric_version(stored)
        verdict = _evaluate(
            item,
            answers[item["id"]],
            stored["text"],
            version,
            tender,
            supplier_id,
            hosts,
            search,
            web,
            venue_model,
            content_model,
            experience_model,
            host_model,
            music_model,
        )
        submit(
            bid_id,
            verdict.requirement_id,
            verdict.score,
            verdict.justification,
            list(verdict.sources),
            version,
        )
        if item["is_threshold"] and verdict.score < 6:
            return


def _require_implemented(items: list[dict]) -> None:
    """Raise before any submit so an unmapped type cannot be skipped."""
    unknown = [item.get("type") for item in items if item.get("type") not in _IMPLEMENTED]
    if unknown:
        names = ", ".join(repr(kind) for kind in unknown)
        registered = ", ".join(sorted(_IMPLEMENTED))
        raise ValueError(
            f"No sub-agent for requirement type {names}. Registered types: {registered}."
        )


def _approved_hosts(items: list[dict], payload: dict) -> tuple[str, ...]:
    """The seeded list, required whenever a HOST requirement is present."""
    if not any(item.get("type") == "HOST" for item in items):
        return ()
    if "approved_hosts" not in payload:
        raise ValueError("approved_hosts is required when a HOST requirement exists.")
    hosts = payload["approved_hosts"]
    if not isinstance(hosts, list) or any(type(host) is not str for host in hosts):
        raise ValueError("approved_hosts must be a list of strings.")
    return tuple(hosts)


def _tender_fields(tender: dict) -> tuple[str, str, str]:
    return tender["name"], str(tender["event_date"]), tender["region"]


def _evaluate(
    item: dict,
    answer: str,
    rubric_text: str,
    version: str,
    tender: tuple[str, str, str],
    supplier_id: str,
    approved_hosts: tuple[str, ...],
    search: EvidenceSearch,
    web: PageSearch,
    venue_model: VenueModel,
    content_model: ContentModel,
    experience_model: ExperienceModel,
    host_model: HostModel,
    music_model: MusicModel,
) -> Any:
    name, event_date, region = tender
    kind = item["type"]
    requirement_id = item["id"]
    description = item["description"]
    if kind == "VENUE":
        return evaluate_venue(
            VenueRequirement(id=requirement_id, description=description),
            answer,
            VenueRubric(text=rubric_text, version=version),
            VenueTender(name=name, event_date=event_date, region=region),
            search,
            web,
            venue_model,
        )
    if kind == "CONTENT":
        return evaluate_content(
            ContentRequirement(id=requirement_id, description=description),
            answer,
            ContentRubric(text=rubric_text, version=version),
            ContentTender(name=name, event_date=event_date, region=region),
            search,
            web,
            content_model,
        )
    if kind == "HOST":
        return evaluate_host(
            HostRequirement(id=requirement_id, description=description),
            answer,
            HostRubric(text=rubric_text, version=version),
            HostTender(name=name, event_date=event_date, region=region),
            approved_hosts,
            web,
            host_model,
        )
    if kind == "MUSIC":
        return evaluate_music(
            MusicRequirement(id=requirement_id, description=description),
            answer,
            MusicRubric(text=rubric_text, version=version),
            MusicTender(name=name, event_date=event_date, region=region),
            search,
            web,
            music_model,
        )
    if kind == "EXPERIENCE":
        return evaluate_experience(
            ExperienceRequirement(id=requirement_id, description=description),
            answer,
            ExperienceRubric(text=rubric_text, version=version),
            ExperienceTender(name=name, event_date=event_date, region=region),
            supplier_id,
            search,
            web,
            experience_model,
        )
    raise ValueError(f"No sub-agent for requirement type {kind!r}.")


def _rubric_version(stored: dict) -> str:
    version = stored.get("metadata", {}).get("rubric_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("rubric_version is required.")
    return version
