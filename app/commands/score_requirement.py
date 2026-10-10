"""ScoreRequirement command (`02-architecture.md` §3 and §7).

The handler replays the bid stream, checks the scoring invariants, and appends
`RequirementScored`. That SQL handler arrives with the aggregates. Callers
depend on this protocol so the MCP tool can be tested without the event store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ScoreRequirement:
    """The command the tool dispatches. `scored_by` is always `"agent"`."""

    bid_id: str
    requirement_id: str
    score: int
    justification: str
    sources: tuple[str, ...]
    rubric_version: str
    scored_by: str = "agent"


class ScoreRequirementHandler(Protocol):
    def handle(self, command: ScoreRequirement) -> dict:
        """Return `{"accepted": bool, "event_version": int}`."""
        ...


class ScoringRejected(Exception):
    """The bid cannot be scored. The tool leaves this exception unchanged."""


class SqlScoreRequirementHandler:
    """Appends RequirementScored once the aggregates exist.

    Until then a call fails closed. It does not write SQL from the MCP process
    by reaching into the events table on its own.
    """

    def handle(self, command: ScoreRequirement) -> dict:
        raise NotImplementedError(
            "ScoreRequirement SQL handler arrives with the aggregates."
        )
