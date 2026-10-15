"""Deterministic scoring (`04-scoring-model.md` §3, `NFR-DET-1`).

No I/O and no model calls. Quality, price score, and final score stay at full
precision; presentation rounds to one decimal (`T-SC-19`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


def quality(parts: Sequence[tuple[int, int]]) -> float:
    """Quality on a 0–100 scale (`T-SC-1`, `T-SC-2`).

    Each part is `(weight, score)`. Weight is a percent. The score is an
    integer 0–10. When the weights sum to 100 this is `sum(weight * score) / 10`.
    """
    weighted = sum(weight * score for weight, score in parts)
    total_weight = sum(weight for weight, _score in parts)
    return weighted * 100 / (10 * total_weight)


def _require_positive_price(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be an integer greater than 0")


def price_score(bid_price: int, lowest_qualified_price: int) -> float:
    """`(lowest qualified price / bid price) * 100` (`04` §5, `T-SC-4`).

    Full precision. The cheapest qualified bid scores 100.
    Both arguments are integers greater than 0 (`T-SC-20`).
    """
    _require_positive_price(bid_price, "bid_price")
    _require_positive_price(lowest_qualified_price, "lowest_qualified_price")
    return lowest_qualified_price / bid_price * 100


def final_score(quality_score: float, price_component: float, alpha: float) -> float:
    """`α × quality + (1 − α) × price score` (`04` §6, `T-SC-6`).

    Full precision. Nothing is rounded before the multiplication.
    """
    return alpha * quality_score + (1 - alpha) * price_component


@dataclass(frozen=True)
class Standing:
    """One bid ready for ranking. Qualification is already decided."""

    bid_id: str
    quality: float
    price: int
    qualified: bool
    submitted_at: datetime


@dataclass(frozen=True)
class RankedBid:
    """One qualified bid after price scoring and ranking (`04` §5–§6)."""

    bid_id: str
    quality: float
    price: int
    price_score: float
    final_score: float
    rank: int


def rank_bids(standings: Sequence[Standing], alpha: float) -> tuple[RankedBid, ...]:
    """Rank qualified bids by final score, highest first (`T-SC-7`).

    The price pool is the qualified standings only. A disqualified standing
    is omitted. With no qualified bid the ranking is empty. Equal finals
    break by higher quality, then by earlier `submitted_at` (`T-SC-16`,
    `T-SC-17`). A residual tie keeps input order.
    """
    qualified = [standing for standing in standings if standing.qualified]
    if not qualified:
        return ()
    lowest = min(standing.price for standing in qualified)
    scored = []
    for standing in qualified:
        component = price_score(standing.price, lowest)
        scored.append((standing, component, final_score(standing.quality, component, alpha)))
    scored.sort(key=lambda item: (-item[2], -item[0].quality, item[0].submitted_at))
    return tuple(
        RankedBid(
            bid_id=standing.bid_id,
            quality=standing.quality,
            price=standing.price,
            price_score=component,
            final_score=total,
            rank=index,
        )
        for index, (standing, component, total) in enumerate(scored, start=1)
    )


# A threshold requirement scores 6 or above (`04` §4).
_THRESHOLD_PASS_MARK = 6


def fails_threshold(score: int) -> bool:
    """True when a threshold requirement's score is below the pass mark."""
    return score < _THRESHOLD_PASS_MARK


# Fixed system-wide (`04` §4, `FR-EVAL-4`).
_QUALITY_FLOOR = 60


def below_quality_floor(quality_score: float) -> bool:
    """True when a fully scored bid is below 60."""
    return quality_score < _QUALITY_FLOOR
