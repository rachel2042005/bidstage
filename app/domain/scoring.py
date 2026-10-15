"""Deterministic price score, final score, and rank (NFR-DET-1).

The agent never calls this module. Presentation rounds to one decimal only
after the final multiplication (specs/04-scoring-model.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class QualifiedBid:
    bid_id: str
    quality: Decimal
    price_amount: int
    submitted_at: datetime


@dataclass(frozen=True)
class RankedBid:
    bid_id: str
    quality: Decimal
    price_amount: int
    price_score: Decimal
    final_score: Decimal
    rank: int


def present(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def rank_bids(bids: list[QualifiedBid], alpha: Decimal) -> list[RankedBid]:
    if not bids:
        return []
    lowest = min(bid.price_amount for bid in bids)
    scored: list[tuple[QualifiedBid, Decimal, Decimal]] = []
    for bid in bids:
        price_score = Decimal(lowest) / Decimal(bid.price_amount) * Decimal(100)
        final = alpha * bid.quality + (Decimal(1) - alpha) * price_score
        scored.append((bid, price_score, final))
    scored.sort(key=lambda item: (-item[2], -item[0].quality, item[0].submitted_at))
    return [
        RankedBid(
            bid_id=bid.bid_id,
            quality=present(bid.quality),
            price_amount=bid.price_amount,
            price_score=present(price_score),
            final_score=present(final),
            rank=index,
        )
        for index, (bid, price_score, final) in enumerate(scored, start=1)
    ]
