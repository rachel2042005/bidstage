"""The project's own MCP tools — exactly two (`NFR-TOOL-1`).

`get_tender_requirements` is implemented. `submit_requirement_score` stays a
stub until the next part. Contracts: `specs/02-architecture.md` §7.
"""

from __future__ import annotations

from typing import Any

from app import bootstrap

bootstrap.init()

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


def submit_requirement_score(
    bid_id: str,
    requirement_id: str,
    score: int,
    justification: str,
    sources: list[str],
) -> dict[str, Any]:
    """Persists one requirement score as a RequirementScored event.

    Validates 0 <= score <= 10 and appends; it is not a raw write. Idempotent
    on (bid_id, requirement_id), so a retry after a timeout cannot double-score.

    Returns {"accepted": bool, "event_version": int}.
    """
    raise NotImplementedError("Phase 4.")


@mcp.tool(name="get_tender_requirements")
def get_tender_requirements_tool(tender_id: str) -> dict[str, Any]:
    """Requirements, weights, and threshold flags for one tender.

    Includes approved_hosts only when a requirement has type HOST.
    Returns no bid prices.
    """
    return get_tender_requirements(tender_id, SqlTenderRequirementsQuery())
