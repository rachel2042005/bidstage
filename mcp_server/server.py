"""The project's own MCP tools — exactly two (`NFR-TOOL-1`).

`get_tender_requirements` and `submit_requirement_score` are both implemented.
Contracts: `specs/02-architecture.md` §7.
"""

from __future__ import annotations

from typing import Any

from app import bootstrap

bootstrap.init()

from app.commands.score_requirement import (  # noqa: E402
    ScoreRequirement,
    ScoreRequirementHandler,
    SqlScoreRequirementHandler,
)
from app.queries.tender_requirements import (  # noqa: E402
    SqlTenderRequirementsQuery,
    TenderRequirementsQuery,
)

import fastmcp  # noqa: E402

mcp = fastmcp.FastMCP("BidStage")

# §7 fields only. Anything else on the view, including price, is dropped (T-AG-2).
_TENDER_FIELDS = ("name", "type", "event_date", "region", "alpha")
_REQUIREMENT_FIELDS = ("id", "type", "weight", "is_threshold", "description")


class TenderNotFound(LookupError):
    """No tender exists for the requested id."""


def get_tender_requirements(
    tender_id: str,
    query: TenderRequirementsQuery,
) -> dict[str, Any]:
    """Requirements, weights and threshold flags for one tender (`T-AG-2`).

    Returns no bid prices, by construction: only the §7 fields are copied.
    For tenders carrying a HOST requirement the response also includes
    `approved_hosts`, read from the seeded reference table — which is why no
    third tool is needed (`T-AG-3`). A blank id raises `ValueError`. An unknown
    id raises `TenderNotFound`.
    """
    if not tender_id.strip():
        raise ValueError("tender_id is required.")
    view = query.fetch(tender_id)
    if view is None:
        raise TenderNotFound(f"No tender with id {tender_id!r}.")
    payload: dict[str, Any] = {
        "tender": {field: view["tender"][field] for field in _TENDER_FIELDS},
        "requirements": [
            {field: requirement[field] for field in _REQUIREMENT_FIELDS}
            for requirement in view["requirements"]
        ],
    }
    if any(requirement["type"] == "HOST" for requirement in payload["requirements"]):
        payload["approved_hosts"] = list(view["approved_hosts"])
    return payload


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required.")


def submit_requirement_score(
    bid_id: str,
    requirement_id: str,
    score: int,
    justification: str,
    sources: list[str],
    rubric_version: str,
    handler: ScoreRequirementHandler,
) -> dict[str, Any]:
    """Dispatch one RequirementScored command (`FR-EVAL-2`).

    The handler appends the event and returns `{"accepted", "event_version"}`.
    `scored_by` is stamped `"agent"` here; the caller cannot set it.
    A score outside 0..10, or any non-int including bool, raises ValueError.
    """
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 10:
        raise ValueError("score must be an integer from 0 to 10.")
    _require_text(bid_id, "bid_id")
    _require_text(requirement_id, "requirement_id")
    _require_text(justification, "justification")
    _require_text(rubric_version, "rubric_version")
    if not isinstance(sources, list) or any(
        not isinstance(source, str) or not source.strip() for source in sources
    ):
        raise ValueError("sources must be a list of non-empty strings.")
    command = ScoreRequirement(
        bid_id=bid_id,
        requirement_id=requirement_id,
        score=score,
        justification=justification,
        sources=tuple(sources),
        rubric_version=rubric_version,
        scored_by="agent",
    )
    return handler.handle(command)


@mcp.tool(name="get_tender_requirements")
def get_tender_requirements_tool(tender_id: str) -> dict[str, Any]:
    """Requirements, weights, and threshold flags for one tender.

    Includes approved_hosts only when a requirement has type HOST.
    Returns no bid prices.
    """
    return get_tender_requirements(tender_id, SqlTenderRequirementsQuery())


@mcp.tool(name="submit_requirement_score")
def submit_requirement_score_tool(
    bid_id: str,
    requirement_id: str,
    score: int,
    justification: str,
    sources: list[str],
    rubric_version: str,
) -> dict[str, Any]:
    """Record one requirement score. score is an integer from 0 to 10.

    sources may be empty. The handler stamps scored_by as agent.
    """
    return submit_requirement_score(
        bid_id,
        requirement_id,
        score,
        justification,
        sources,
        rubric_version,
        SqlScoreRequirementHandler(),
    )
