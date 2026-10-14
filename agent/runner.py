"""Evaluation agent entry point — a process independent of the web app (NFR-AGT-2).

Run with: python -m agent.runner

`run_once` claims one bid and finishes its assessment (`T-AG-27`). The poll
loop is `run_forever` in `agent.worker`. `main` does not start it: the claim
projection `rm_bid_for_assessment` is not connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app import bootstrap

bootstrap.init()

from app.commands.complete_assessment import (  # noqa: E402
    BidDisqualified,
    QualityAssessmentCompleted,
    RequirementScore,
)
from app.config import load_settings  # noqa: E402
from app.domain.scoring import below_quality_floor, fails_threshold, quality  # noqa: E402
from agent.orchestrator import (  # noqa: E402
    ContentModel,
    EvidenceSearch,
    ExperienceModel,
    HostModel,
    MusicModel,
    PageSearch,
    RequirementsTool,
    RubricIndex,
    ScoreTool,
    VenueModel,
    score_bid,
)


@dataclass(frozen=True)
class AssessmentWork:
    """One claimed bid. Price is absent (`NFR-AGT-4`)."""

    bid_id: str
    tender_id: str
    supplier_id: str
    answers: Mapping[str, str]


class AssessmentQueue(Protocol):
    """Operational take. A second claim of the same bid returns nothing."""

    def claim(self) -> AssessmentWork | None: ...


class Completion(Protocol):
    """`CompleteAssessment` handler."""

    def __call__(self, command: QualityAssessmentCompleted | BidDisqualified) -> None: ...


def run_once(
    queue: AssessmentQueue,
    *,
    requirements: RequirementsTool,
    rubrics: RubricIndex,
    search: EvidenceSearch,
    web: PageSearch,
    venue_model: VenueModel,
    content_model: ContentModel,
    experience_model: ExperienceModel,
    host_model: HostModel,
    music_model: MusicModel,
    submit: ScoreTool,
    complete: Completion,
) -> int:
    """Claim one bid, score it, and record the terminal command (`T-AG-27`, `T-AG-28`, `T-AG-29`)."""
    work = queue.claim()
    if work is None:
        return 0
    recorded: list[tuple[str, int]] = []

    def wrapping_submit(
        bid_id: str,
        requirement_id: str,
        score: int,
        justification: str,
        sources: list[str],
        rubric_version: str,
    ) -> dict[str, Any]:
        recorded.append((requirement_id, score))
        return submit(
            bid_id, requirement_id, score, justification, sources, rubric_version
        )

    score_bid(
        work.bid_id,
        work.tender_id,
        work.supplier_id,
        work.answers,
        requirements=requirements,
        rubrics=rubrics,
        search=search,
        web=web,
        venue_model=venue_model,
        content_model=content_model,
        experience_model=experience_model,
        host_model=host_model,
        music_model=music_model,
        submit=wrapping_submit,
    )
    payload = requirements(work.tender_id)
    by_id = {item["id"]: item for item in payload["requirements"]}
    for requirement_id, score in recorded:
        if by_id[requirement_id]["is_threshold"] and fails_threshold(score):
            complete(
                BidDisqualified(
                    bid_id=work.bid_id,
                    reason="threshold",
                    failed_requirement_id=requirement_id,
                )
            )
            return 1
    requirement_scores = tuple(
        RequirementScore(requirement_id, score, by_id[requirement_id]["weight"])
        for requirement_id, score in recorded
    )
    quality_score = quality(tuple((item.weight, item.score) for item in requirement_scores))
    if below_quality_floor(quality_score):
        complete(
            BidDisqualified(
                bid_id=work.bid_id,
                reason="quality_floor",
                quality_score=quality_score,
            )
        )
        return 1
    complete(
        QualityAssessmentCompleted(
            bid_id=work.bid_id,
            quality_score=quality_score,
            requirement_scores=requirement_scores,
        )
    )
    return 1


def main() -> None:
    """Print the poll interval. `run_forever` stays unwired until bids can be claimed."""
    settings = load_settings()
    print(f"Agent started; polling every {settings.agent_poll_seconds}s.")
    print("rm_bid_for_assessment is not connected; no bids claimed.")


if __name__ == "__main__":
    main()
