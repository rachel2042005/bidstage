"""Evaluation agent entry point — a process independent of the web app (NFR-AGT-2).

Run with: python -m agent.runner

`run_once` claims one bid and finishes its assessment (`T-AG-27`).
`main(["--once"])` runs that cycle once (`T-AG-41`). With no flag, `main`
calls `run_forever` (`T-AG-42`). `build_runtime` supplies the queue and a
`complete` callable on its own transaction. The other ports stay parameters.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from collections.abc import Callable
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


@dataclass(frozen=True)
class AgentRuntime:
    """Dependencies for one agent process. `complete` uses its own transaction."""

    queue: AssessmentQueue
    requirements: RequirementsTool
    rubrics: RubricIndex
    search: EvidenceSearch
    web: PageSearch
    venue_model: VenueModel
    content_model: ContentModel
    experience_model: ExperienceModel
    host_model: HostModel
    music_model: MusicModel
    submit: ScoreTool
    complete: Completion


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


def build_runtime(
    settings: Any,
    *,
    requirements: RequirementsTool,
    search: EvidenceSearch,
    web: PageSearch,
    venue_model: VenueModel,
    content_model: ContentModel,
    experience_model: ExperienceModel,
    host_model: HostModel,
    music_model: MusicModel,
    submit: ScoreTool,
) -> Any:
    """Queue and completion on separate connections. Rubrics come from Chroma."""
    from app.commands.complete_assessment import SqlCompleteAssessmentHandler
    from app.events.db import connect, transaction
    from app.infrastructure.vector_store import ChromaVectorStore
    from app.projections.bid_for_assessment import SqlAssessmentQueue
    from app.services.semantic_search_service import SemanticSearchService

    queue = SqlAssessmentQueue(connect())
    rubrics = SemanticSearchService(ChromaVectorStore(settings))

    def complete(command: QualityAssessmentCompleted | BidDisqualified) -> None:
        with transaction() as conn:
            SqlCompleteAssessmentHandler(conn).handle(command)

    return AgentRuntime(
        queue=queue,
        requirements=requirements,
        rubrics=rubrics,
        search=search,
        web=web,
        venue_model=venue_model,
        content_model=content_model,
        experience_model=experience_model,
        host_model=host_model,
        music_model=music_model,
        submit=submit,
        complete=complete,
    )


def main(
    argv: list[str] | None = None,
    *,
    runtime: Callable[[Any], Any] | None = None,
    cycle: Callable[..., int] = run_once,
    forever: Callable[..., None] | None = None,
) -> None:
    """Run one cycle, or the poll loop, with dependencies from `runtime`."""
    settings = load_settings()
    args = sys.argv[1:] if argv is None else list(argv)
    if runtime is None:
        runtime = build_runtime
    deps = runtime(settings)
    bound = _deps(deps)
    if args == ["--once"]:
        cycle(**bound)
        return
    if args:
        raise SystemExit(2)
    if forever is None:
        from agent.worker import run_forever

        forever = run_forever
    stop, restore = _install_stop()
    try:
        forever(
            **bound,
            interval=settings.agent_poll_seconds,
            sleep=time.sleep,
            stop=stop,
            cycle=cycle,
        )
    finally:
        restore()


def _deps(deps: Any) -> dict[str, Any]:
    return {
        "queue": deps.queue,
        "requirements": deps.requirements,
        "rubrics": deps.rubrics,
        "search": deps.search,
        "web": deps.web,
        "venue_model": deps.venue_model,
        "content_model": deps.content_model,
        "experience_model": deps.experience_model,
        "host_model": deps.host_model,
        "music_model": deps.music_model,
        "submit": deps.submit,
        "complete": deps.complete,
    }


def _install_stop() -> tuple[Callable[[], bool], Callable[[], None]]:
    flag = threading.Event()
    previous: list[tuple[signal.Signals, Any]] = []

    def handler(signum: int, frame: object) -> None:
        del signum, frame
        flag.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous.append((sig, signal.signal(sig, handler)))
        except (ValueError, OSError):
            continue

    def restore() -> None:
        for sig, old in reversed(previous):
            signal.signal(sig, old)

    return flag.is_set, restore


if __name__ == "__main__":
    main()
