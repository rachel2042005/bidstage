"""The project's own MCP tools — exactly two (NFR-TOOL-1).

Contracts: specs/02-architecture.md §7. Implementations arrive in Phase 4;
the signatures are fixed here because the agent is written against them.
"""

from __future__ import annotations

from typing import Any

from app import bootstrap

bootstrap.init()


def get_tender_requirements(tender_id: str) -> dict[str, Any]:
    """Requirements, weights and threshold flags for one tender.

    Returns no bid prices, by construction. For tenders carrying a HOST
    requirement the response also includes `approved_hosts`, read from the
    seeded reference table — which is why no third tool is needed.

    Shape:
        {
          "tender": {"name", "type", "event_date", "region", "alpha"},
          "requirements": [
            {"id", "type", "weight", "is_threshold", "description"}
          ],
          "approved_hosts": ["..."]   # only when a HOST requirement exists
        }
    """
    raise NotImplementedError("Phase 4.")


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
