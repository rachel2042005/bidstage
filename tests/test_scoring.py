"""Quality aggregation (`04-scoring-model.md` §3).

Seam: `quality`. Expected values are the worked-example literals.
No price score and no final score.

T-SC-1 — supplier A, weighted sum 820, quality 82.0.
T-SC-2 — supplier B, weighted sum 740, quality 74.0.
"""

from app.domain.scoring import quality

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
