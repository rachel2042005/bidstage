"""Assessment lifecycle (`05-agent-spec.md` §4).

Seam: `run_once`. The queue, the score handler, and the completion handler are
in-memory. `score_bid` and the three evaluators are real. Search, web, and
the models are the orchestrator fakes.

T-AG-27 — weights 40/30/30 and three scores of 9 complete at quality 90.0.
A second call claims nothing.
T-AG-28 — the VENUE threshold scores 4 and the bid is disqualified.
T-AG-29 — scores 5, 4, and 6 finish every requirement at quality 50.0.
T-AG-41 — `main(["--once"])` runs one cycle and does not start the poll loop.
T-AG-42 — `main([])` starts the poll loop at `settings.agent_poll_seconds`.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.subagents.content import ContentAssessment, ContentMaterial
from agent.subagents.experience import ExperienceAssessment, ExperienceMaterial
from agent.subagents.venue import VenueAssessment, VenueMaterial
from app.commands.complete_assessment import (
    BidDisqualified,
    QualityAssessmentCompleted,
    RequirementScore,
)
from app.config import load_settings
from app.services.semantic_search_service import SemanticSearchService
from agent.runner import AssessmentWork, main, run_once
from mcp_server.server import get_tender_requirements, submit_requirement_score
from tests.test_orchestrator import (
    _BID,
    _CONTENT_ANSWER,
    _CONTENT_ID,
    _CONTENT_JUSTIFICATION,
    _CONTENT_VERSION,
    _EXPERIENCE_ANSWER,
    _EXPERIENCE_ID,
    _EXPERIENCE_JUSTIFICATION,
    _EXPERIENCE_VERSION,
    _Requirements,
    _RubricStore,
    _SUPPLIER,
    _Scores,
    _Search,
    _TENDER_ID,
    _VENUE_ANSWER,
    _VENUE_DESCRIPTION,
    _VENUE_ID,
    _VENUE_JUSTIFICATION,
    _VENUE_RUBRIC,
    _VENUE_VERSION,
    _CONTENT_DESCRIPTION,
    _CONTENT_RUBRIC,
    _EXPERIENCE_DESCRIPTION,
    _EXPERIENCE_RUBRIC,
    _PAGE_TEXT,
    _PAGE_URL,
    _REGULATION,
    _CONCEPT,
    _PORTFOLIO,
    _Web,
    _command,
    _tender_matches,
    _view,
)
from tests.test_orchestrator import _ContentModel, _ExperienceModel, _UnusedModel, _VenueModel


class _Queue:
    def __init__(self, work: AssessmentWork) -> None:
        self._work: AssessmentWork | None = work

    def claim(self) -> AssessmentWork | None:
        work = self._work
        self._work = None
        return work


class _Completions:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def handle(self, command: object) -> None:
        self.commands.append(command)


def _work() -> AssessmentWork:
    return AssessmentWork(
        bid_id=_BID,
        tender_id=_TENDER_ID,
        supplier_id=_SUPPLIER,
        answers={
            _VENUE_ID: _VENUE_ANSWER,
            _CONTENT_ID: _CONTENT_ANSWER,
            _EXPERIENCE_ID: _EXPERIENCE_ANSWER,
        },
    )


def _run(
    queue: _Queue,
    scores: _Scores,
    completions: _Completions,
    *,
    venue_model: _VenueModel,
    content_model: _ContentModel,
    experience_model: _ExperienceModel,
    view: dict[str, dict] | None = None,
) -> int:
    return run_once(
        queue,
        requirements=lambda tender_id: get_tender_requirements(
            tender_id, _Requirements(_view() if view is None else view)
        ),
        rubrics=SemanticSearchService(_RubricStore()),
        search=_Search(),
        web=_Web(),
        venue_model=venue_model,
        content_model=content_model,
        experience_model=experience_model,
        host_model=_UnusedModel(),
        music_model=_UnusedModel(),
        submit=lambda bid_id, requirement_id, score, justification, sources, rubric_version: (
            submit_requirement_score(
                bid_id,
                requirement_id,
                score,
                justification,
                sources,
                rubric_version,
                scores,
            )
        ),
        complete=completions.handle,
    )


def test_claimed_bid_completes_at_90() -> None:
    """T-AG-27. Three nines. The next poll finds nothing left to claim."""
    queue = _Queue(_work())
    scores = _Scores()
    completions = _Completions()
    venue = _VenueModel()
    content = _ContentModel()
    experience = _ExperienceModel()

    assessed = _run(
        queue,
        scores,
        completions,
        venue_model=venue,
        content_model=content,
        experience_model=experience,
    )
    again = _run(
        queue,
        scores,
        completions,
        venue_model=venue,
        content_model=content,
        experience_model=experience,
    )

    assert assessed == 1
    assert again == 0
    assert scores.commands == [
        _command(_VENUE_ID, 9, _VENUE_JUSTIFICATION, _VENUE_VERSION),
        _command(_CONTENT_ID, 9, _CONTENT_JUSTIFICATION, _CONTENT_VERSION),
        _command(_EXPERIENCE_ID, 9, _EXPERIENCE_JUSTIFICATION, _EXPERIENCE_VERSION),
    ]
    assert completions.commands == [
        QualityAssessmentCompleted(
            bid_id=_BID,
            quality_score=90.0,
            requirement_scores=(
                RequirementScore(requirement_id=_VENUE_ID, score=9, weight=40),
                RequirementScore(requirement_id=_CONTENT_ID, score=9, weight=30),
                RequirementScore(requirement_id=_EXPERIENCE_ID, score=9, weight=30),
            ),
        )
    ]


def test_threshold_failure_disqualifies_the_bid() -> None:
    """T-AG-28. VENUE scores 4. CONTENT and EXPERIENCE are not assessed."""
    queue = _Queue(_work())
    scores = _Scores()
    completions = _Completions()
    content = _ContentModel()
    experience = _ExperienceModel()

    assessed = _run(
        queue,
        scores,
        completions,
        venue_model=_VenueModel(breached=True),
        content_model=content,
        experience_model=experience,
    )

    assert assessed == 1
    assert content.calls == 0
    assert experience.calls == 0
    assert scores.commands == [
        _command(_VENUE_ID, 4, "A retrieved obligation is breached.", _VENUE_VERSION),
    ]
    assert completions.commands == [
        BidDisqualified(
            bid_id=_BID,
            reason="threshold",
            failed_requirement_id=_VENUE_ID,
        )
    ]


_FLOOR_VENUE = "Official capacity was not found."
_FLOOR_CONTENT = "The schedule contradicts itself beside a relevant concept."
_FLOOR_EXPERIENCE = "An outdoor festival was claimed and neither verified nor disproved."


def _floor_view() -> dict[str, dict]:
    view = _view()
    for item in view[_TENDER_ID]["requirements"]:
        item["is_threshold"] = False
    return view


class _FloorVenue:
    def assess(self, material: VenueMaterial) -> VenueAssessment:
        ready = (
            material.answer == _VENUE_ANSWER
            and material.requirement.id == _VENUE_ID
            and material.requirement.description == _VENUE_DESCRIPTION
            and material.rubric_text == _VENUE_RUBRIC
            and _tender_matches(material.tender)
            and _REGULATION in "\n".join(material.regulations)
            and _PAGE_TEXT in "\n".join(document.content for document in material.web_results)
        )
        if not ready:
            return VenueAssessment(
                proposed_score=9,
                justification=_VENUE_JUSTIFICATION,
                sources=(_PAGE_URL,),
                verifications=(),
                capacity_claim="verified",
                meets_tender_capacity=True,
                regulation_breached=False,
            )
        return VenueAssessment(
            proposed_score=5,
            justification=_FLOOR_VENUE,
            sources=(_PAGE_URL,),
            verifications=(),
            capacity_claim="inconclusive",
            meets_tender_capacity=False,
            regulation_breached=False,
        )


class _FloorContent:
    def assess(self, material: ContentMaterial) -> ContentAssessment:
        ready = (
            material.answer == _CONTENT_ANSWER
            and material.requirement.id == _CONTENT_ID
            and material.requirement.description == _CONTENT_DESCRIPTION
            and material.rubric_text == _CONTENT_RUBRIC
            and _tender_matches(material.tender)
            and _CONCEPT in "\n".join(material.concepts)
            and _PAGE_TEXT in "\n".join(document.content for document in material.web_results)
        )
        if not ready:
            return ContentAssessment(
                proposed_score=9,
                justification=_CONTENT_JUSTIFICATION,
                sources=(_PAGE_URL,),
                verifications=(),
                performer_claim="verified",
                both_components_sound=True,
                substantive_defect=False,
            )
        return ContentAssessment(
            proposed_score=4,
            justification=_FLOOR_CONTENT,
            sources=(_PAGE_URL,),
            verifications=(),
            performer_claim="inconclusive",
            both_components_sound=False,
            substantive_defect=True,
        )


class _FloorExperience:
    def assess(self, material: ExperienceMaterial) -> ExperienceAssessment:
        ready = (
            material.answer == _EXPERIENCE_ANSWER
            and material.requirement.id == _EXPERIENCE_ID
            and material.requirement.description == _EXPERIENCE_DESCRIPTION
            and material.rubric_text == _EXPERIENCE_RUBRIC
            and material.supplier_id == _SUPPLIER
            and _tender_matches(material.tender)
            and _PORTFOLIO in "\n".join(material.portfolios)
            and _PAGE_TEXT in "\n".join(document.content for document in material.web_results)
        )
        if not ready:
            return ExperienceAssessment(
                proposed_score=9,
                justification=_EXPERIENCE_JUSTIFICATION,
                sources=(_PAGE_URL,),
                verifications=(),
                any_disproved=False,
                verified_outdoor_count=3,
                outdoor_claimed=True,
            )
        return ExperienceAssessment(
            proposed_score=6,
            justification=_FLOOR_EXPERIENCE,
            sources=(_PAGE_URL,),
            verifications=(),
            any_disproved=False,
            verified_outdoor_count=0,
            outdoor_claimed=True,
        )


def test_quality_below_60_disqualifies_the_bid() -> None:
    """T-AG-29. Scores 5, 4, and 6. Quality is 50.0, under the floor."""
    queue = _Queue(_work())
    scores = _Scores()
    completions = _Completions()

    assessed = _run(
        queue,
        scores,
        completions,
        venue_model=_FloorVenue(),
        content_model=_FloorContent(),
        experience_model=_FloorExperience(),
        view=_floor_view(),
    )

    assert assessed == 1
    assert scores.commands == [
        _command(_VENUE_ID, 5, _FLOOR_VENUE, _VENUE_VERSION),
        _command(_CONTENT_ID, 4, _FLOOR_CONTENT, _CONTENT_VERSION),
        _command(_EXPERIENCE_ID, 6, _FLOOR_EXPERIENCE, _EXPERIENCE_VERSION),
    ]
    assert completions.commands == [
        BidDisqualified(
            bid_id=_BID,
            reason="quality_floor",
            quality_score=50.0,
        )
    ]


def _dispatch_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        queue=object(),
        requirements=object(),
        rubrics=object(),
        search=object(),
        web=object(),
        venue_model=object(),
        content_model=object(),
        experience_model=object(),
        host_model=object(),
        music_model=object(),
        submit=object(),
        complete=object(),
    )


def test_once_runs_a_single_cycle() -> None:
    """T-AG-41. One cycle receives the runtime. The poll loop stays idle."""
    runtime = _dispatch_runtime()
    seen: dict[str, object] = {}

    def cycle(queue: object, **kwargs: object) -> int:
        seen["queue"] = queue
        seen["kwargs"] = kwargs
        return 0

    def forever(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forever")

    main(["--once"], runtime=lambda _settings: runtime, cycle=cycle, forever=forever)

    assert seen["queue"] is runtime.queue
    kwargs = seen["kwargs"]
    assert kwargs["complete"] is runtime.complete
    assert kwargs["venue_model"] is runtime.venue_model
    assert kwargs["content_model"] is runtime.content_model
    assert kwargs["experience_model"] is runtime.experience_model
    assert kwargs["host_model"] is runtime.host_model
    assert kwargs["music_model"] is runtime.music_model
    assert kwargs["requirements"] is runtime.requirements
    assert kwargs["rubrics"] is runtime.rubrics
    assert kwargs["search"] is runtime.search
    assert kwargs["web"] is runtime.web
    assert kwargs["submit"] is runtime.submit


def test_default_starts_the_poll_loop() -> None:
    """T-AG-42. The loop receives the runtime and the configured poll interval."""
    runtime = _dispatch_runtime()
    seen: dict[str, object] = {}

    def cycle(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("cycle")

    def forever(queue: object, **kwargs: object) -> None:
        seen["queue"] = queue
        seen["kwargs"] = kwargs

    main([], runtime=lambda _settings: runtime, cycle=cycle, forever=forever)

    assert seen["queue"] is runtime.queue
    kwargs = seen["kwargs"]
    assert kwargs["interval"] == load_settings().agent_poll_seconds
    assert kwargs["complete"] is runtime.complete
    assert kwargs["venue_model"] is runtime.venue_model
    assert kwargs["content_model"] is runtime.content_model
    assert kwargs["experience_model"] is runtime.experience_model
    assert kwargs["host_model"] is runtime.host_model
    assert kwargs["music_model"] is runtime.music_model
    assert kwargs["requirements"] is runtime.requirements
    assert kwargs["rubrics"] is runtime.rubrics
    assert kwargs["search"] is runtime.search
    assert kwargs["web"] is runtime.web
    assert kwargs["submit"] is runtime.submit
