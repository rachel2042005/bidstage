"""Quality, price score, final score, and ranking (`04-scoring-model.md`).

Seams: `quality`, `price_score`, `final_score`, `rank_bids`.
Expected values are the worked-example literals. Display uses `round(..., 1)`.

T-SC-1 — supplier A, weighted sum 820, quality 82.0.
T-SC-2 — supplier B, weighted sum 740, quality 74.0.
T-SC-4 — the cheapest qualified bid scores exactly 100.0.
T-SC-3 — supplier A at ₪900,000 against ₪700,000 displays as 77.8.
T-SC-6 — supplier B's final score displays as 84.4.
T-SC-5 — supplier A's final score displays as 80.3.
T-SC-19 — A's final is not the pre-rounded `0.6 × 82 + 0.4 × 77.8`.
T-SC-7 — supplier B ranks above supplier A.
T-SC-8 — a disqualified ₪500,000 bid leaves A and B unchanged.
T-SC-9 — including that ₪500,000 price displays A as 71.4 and B as 73.0.
T-SC-20 — a price that is not an integer greater than 0 raises ValueError.
"""

from datetime import datetime, timezone

import pytest

from app.domain.scoring import Standing, final_score, price_score, quality, rank_bids

# VENUE 25×9, CONTENT 25×9, HOST 15×7, MUSIC 15×7, EXPERIENCE 20×8.
_SUPPLIER_A = ((25, 9), (25, 9), (15, 7), (15, 7), (20, 8))
# VENUE 25×8, CONTENT 25×7, HOST 15×7, MUSIC 15×8, EXPERIENCE 20×7.
_SUPPLIER_B = ((25, 8), (25, 7), (15, 7), (15, 8), (20, 7))


def test_supplier_a_quality_is_82() -> None:
    """T-SC-1. The worked example's weighted sum of 820 is quality 82.0."""
    assert quality(_SUPPLIER_A) == 82.0


def test_supplier_b_quality_is_74() -> None:
    """T-SC-2. The worked example's weighted sum of 740 is quality 74.0."""
    assert quality(_SUPPLIER_B) == 74.0


def test_cheapest_qualified_bid_scores_100() -> None:
    """T-SC-4. Supplier B at ₪700,000 is the lowest qualified price."""
    assert price_score(700_000, 700_000) == 100.0


def test_supplier_a_price_score_displays_as_77_8() -> None:
    """T-SC-3. 700,000 / 900,000 × 100, shown to one decimal."""
    assert round(price_score(900_000, 700_000), 1) == 77.8


def test_supplier_b_final_score_displays_as_84_4() -> None:
    """T-SC-6. Quality 74.0, price score 100, α = 0.6."""
    assert round(final_score(74.0, 100.0, 0.6), 1) == 84.4


def test_supplier_a_final_score_displays_as_80_3() -> None:
    """T-SC-5. Quality 82.0, α = 0.6, price from ₪900,000 against ₪700,000."""
    component = price_score(900_000, 700_000)
    assert round(final_score(82.0, component, 0.6), 1) == 80.3


def test_final_score_is_not_the_pre_rounded_path() -> None:
    """T-SC-19. Rounding the price score to 77.8 before multiplying changes the result."""
    component = price_score(900_000, 700_000)
    full = final_score(82.0, component, 0.6)
    pre_rounded = 0.6 * 82 + 0.4 * 77.8
    assert full != pre_rounded


_SUBMITTED = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_supplier_b_ranks_above_supplier_a() -> None:
    """T-SC-7. B's final beats A's, so B is rank 1."""
    standings = (
        Standing("bid-a", 82.0, 900_000, True, _SUBMITTED),
        Standing("bid-b", 74.0, 700_000, True, _SUBMITTED),
    )
    ranked = rank_bids(standings, 0.6)
    assert [item.bid_id for item in ranked] == ["bid-b", "bid-a"]
    assert [item.rank for item in ranked] == [1, 2]
    assert round(ranked[0].final_score, 1) == 84.4
    assert round(ranked[1].final_score, 1) == 80.3


def test_disqualified_cheapest_bid_does_not_move_the_pool() -> None:
    """T-SC-8. C at ₪500,000 failed a threshold, so A and B keep 77.8 / 100."""
    standings = (
        Standing("bid-a", 82.0, 900_000, True, _SUBMITTED),
        Standing("bid-b", 74.0, 700_000, True, _SUBMITTED),
        Standing("bid-c", 0.0, 500_000, False, _SUBMITTED),
    )
    ranked = {item.bid_id: item for item in rank_bids(standings, 0.6)}
    assert set(ranked) == {"bid-a", "bid-b"}
    assert round(ranked["bid-a"].price_score, 1) == 77.8
    assert round(ranked["bid-a"].final_score, 1) == 80.3
    assert ranked["bid-b"].price_score == 100.0
    assert round(ranked["bid-b"].final_score, 1) == 84.4


def test_contaminated_pool_displays_71_4_and_73_0() -> None:
    """T-SC-9. C's ₪500,000 in the pool moves the displayed finals to 71.4 and 73.0."""
    standings = (
        Standing("bid-a", 82.0, 900_000, True, _SUBMITTED),
        Standing("bid-b", 74.0, 700_000, True, _SUBMITTED),
        Standing("bid-c", 60.0, 500_000, True, _SUBMITTED),
    )
    ranked = {item.bid_id: item for item in rank_bids(standings, 0.6)}
    assert round(ranked["bid-a"].final_score, 1) == 71.4
    assert round(ranked["bid-b"].final_score, 1) == 73.0


@pytest.mark.parametrize(
    ("bid_price", "lowest_qualified_price"),
    [
        (0, 700_000),
        (-1, 700_000),
        (700_000, 0),
        (700_000, -1),
        (True, 700_000),
        (700_000, False),
    ],
)
def test_non_positive_price_is_rejected(bid_price: int, lowest_qualified_price: int) -> None:
    """T-SC-20. 0, a negative, and a bool are not a strictly positive integer price."""
    with pytest.raises(ValueError):
        price_score(bid_price, lowest_qualified_price)
