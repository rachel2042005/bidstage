"""Deterministic scoring (`04-scoring-model.md` §3, `NFR-DET-1`).

No I/O and no model calls. Price score and final score are not in this module yet.
Quality is full precision; presentation rounds to one decimal (`T-SC-19`).
"""

from __future__ import annotations

from collections.abc import Sequence


def quality(parts: Sequence[tuple[int, int]]) -> float:
    """Quality on a 0–100 scale (`T-SC-1`, `T-SC-2`).

    Each part is `(weight, score)`. Weight is a percent. The score is an
    integer 0–10. When the weights sum to 100 this is `sum(weight * score) / 10`.
    """
    weighted = sum(weight * score for weight, score in parts)
    total_weight = sum(weight for weight, _score in parts)
    return weighted * 100 / (10 * total_weight)


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
