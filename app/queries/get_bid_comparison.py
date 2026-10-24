"""GetBidComparison — ranking and requirement-score read models (NFR-CQRS-2).

Missing amounts mean the envelope is still sealed. Category cells come from
dbo.rm_requirement_score. The query never reads dbo.events.
"""

from __future__ import annotations

from decimal import Decimal

from app.projections.bids import ComparisonRow, RequirementScoreView


def get_bid_comparison(bids, tender_id: str, *, sort: str | None = None) -> list[ComparisonRow]:
    rows = list(bids.comparison_for(tender_id))
    if sort == "quality":
        return sorted(rows, key=lambda row: (row.quality is None, _neg(row.quality), row.submitted_at))
    if sort == "price":
        return sorted(
            rows,
            key=lambda row: (row.price_amount is None, row.price_amount if row.price_amount is not None else 0, row.submitted_at),
        )
    if sort == "final":
        return sorted(rows, key=lambda row: (row.final_score is None, _neg(row.final_score), row.submitted_at))
    if any(row.rank is not None for row in rows):
        return sorted(rows, key=lambda row: (row.rank is None, row.rank or 0, row.submitted_at))
    return sorted(rows, key=lambda row: row.submitted_at)


def get_requirement_scores(bids, tender_id: str) -> dict[str, dict[str, RequirementScoreView]]:
    """bid_id → requirement_id → score. Reads rm_requirement_score, never the event stream."""
    reader = getattr(bids, "requirement_scores_for", None)
    if reader is None:
        return {}
    return reader(tender_id)


def _neg(value: Decimal | None) -> Decimal:
    if value is None:
        return Decimal(0)
    return -value
