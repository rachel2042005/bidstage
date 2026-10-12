"""Terminal assessment command (`03-domain-events.md` §2, `05-agent-spec.md` §4).

Not an MCP tool. The handler appends `QualityAssessmentCompleted` or
`BidDisqualified`. The SQL handler arrives with the aggregates; tests use an
in-memory handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RequirementScore:
    """One scored requirement on `QualityAssessmentCompleted`."""

    requirement_id: str
    score: int
    weight: int


@dataclass(frozen=True)
class QualityAssessmentCompleted:
    """Every requirement scored and quality is at least 60."""

    bid_id: str
    quality_score: float
    requirement_scores: tuple[RequirementScore, ...]


@dataclass(frozen=True)
class BidDisqualified:
    """Threshold failure or quality below 60.

    A threshold failure sets `failed_requirement_id` and leaves `quality_score`
    unset. A floor failure sets `quality_score` and leaves `failed_requirement_id`
    unset.
    """

    bid_id: str
    reason: str
    failed_requirement_id: str | None = None
    quality_score: float | None = None


class CompleteAssessmentHandler(Protocol):
    def handle(self, command: QualityAssessmentCompleted | BidDisqualified) -> None: ...
