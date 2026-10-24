"""GetBidComparison — ranking and requirement-score read models (NFR-CQRS-2).

Missing amounts mean the envelope is still sealed. Category cells come from
dbo.rm_requirement_score. The query never reads dbo.events.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.tender import DECIDED
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


@dataclass(frozen=True)
class WinnerCategory:
    label: str
    score: int


@dataclass(frozen=True)
class WinnerSummary:
    supplier_name: str
    final_score: Decimal
    price_amount: int
    categories: tuple[WinnerCategory, ...]


def build_winner_summary(tender, requirements, rows, category_scores, type_labels) -> WinnerSummary | None:
    """The awarded supplier, or nothing while the tender is still open."""
    if tender.status != DECIDED or not tender.winner_bid_id:
        return None
    row = next((item for item in rows if item.bid_id == tender.winner_bid_id), None)
    if row is None or row.final_score is None or row.price_amount is None:
        return None
    scores = category_scores.get(row.bid_id, {})
    categories = []
    for requirement in requirements:
        cell = scores.get(requirement.requirement_id)
        if cell is None:
            continue
        categories.append(
            WinnerCategory(
                label=type_labels.get(requirement.requirement_type, requirement.requirement_type),
                score=int(cell.score),
            )
        )
    return WinnerSummary(
        supplier_name=row.supplier_name,
        final_score=row.final_score,
        price_amount=row.price_amount,
        categories=tuple(categories),
    )


def _neg(value: Decimal | None) -> Decimal:
    if value is None:
        return Decimal(0)
    return -value
