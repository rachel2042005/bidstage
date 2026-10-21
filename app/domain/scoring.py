"""Deterministic scoring (`04-scoring-model.md`, `NFR-DET-1`).

Quality, the threshold mark, and the floor are the agent's inputs. Price score,
final score, and rank for a reveal are `Decimal`. The agent never calls this
module for a price. Presentation rounds to one decimal only after the final
multiplication.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


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
    """One bid ready for the worked-example ranking. Qualification is already decided."""

    bid_id: str
    quality: float
    price: int
    qualified: bool
    submitted_at: datetime


@dataclass(frozen=True)
class QualifiedBid:
    bid_id: str
    quality: Decimal
    price_amount: int
    submitted_at: datetime


@dataclass(frozen=True)
class RankedBid:
    """A ranked bid. `price` is the sealed amount the float ranking records."""

    bid_id: str
    quality: Decimal | float
    price_amount: int
    price_score: Decimal | float
    final_score: Decimal | float
    rank: int

    @property
    def price(self) -> int:
        return self.price_amount


def present(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def rank_bids(
    bids: Sequence[QualifiedBid] | Sequence[Standing],
    alpha: Decimal | float,
) -> list[RankedBid]:
    """Rank by final score, highest first.

    A `Decimal` alpha is the web reveal: every bid is already qualified, and
    the returned scores are quantized to one decimal. A float alpha is the
    worked example: disqualified standings stay out of the price pool, and
    the scores stay at full precision (`T-SC-7`, `T-SC-19`).
    """
    if isinstance(alpha, Decimal):
        return _rank_qualified(bids, alpha)  # type: ignore[arg-type]
    return _rank_standings(bids, float(alpha))  # type: ignore[arg-type]


def _rank_qualified(bids: Sequence[QualifiedBid], alpha: Decimal) -> list[RankedBid]:
    if not bids:
        return []
    lowest = min(bid.price_amount for bid in bids)
    scored: list[tuple[QualifiedBid, Decimal, Decimal]] = []
    for bid in bids:
        component = Decimal(lowest) / Decimal(bid.price_amount) * Decimal(100)
        total = alpha * bid.quality + (Decimal(1) - alpha) * component
        scored.append((bid, component, total))
    scored.sort(key=lambda item: (-item[2], -item[0].quality, item[0].submitted_at))
    return [
        RankedBid(
            bid_id=bid.bid_id,
            quality=present(bid.quality),
            price_amount=bid.price_amount,
            price_score=present(component),
            final_score=present(total),
            rank=index,
        )
        for index, (bid, component, total) in enumerate(scored, start=1)
    ]


def _rank_standings(standings: Sequence[Standing], alpha: float) -> list[RankedBid]:
    qualified = [standing for standing in standings if standing.qualified]
    if not qualified:
        return []
    lowest = min(standing.price for standing in qualified)
    scored: list[tuple[Standing, float, float]] = []
    for standing in qualified:
        component = price_score(standing.price, lowest)
        scored.append((standing, component, final_score(standing.quality, component, alpha)))
    scored.sort(key=lambda item: (-item[2], -item[0].quality, item[0].submitted_at))
    return [
        RankedBid(
            bid_id=standing.bid_id,
            quality=standing.quality,
            price_amount=standing.price,
            price_score=component,
            final_score=total,
            rank=index,
        )
        for index, (standing, component, total) in enumerate(scored, start=1)
    ]


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
