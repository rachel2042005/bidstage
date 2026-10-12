"""Orchestrator dispatch.

Seam: `score_bid`. The real `get_tender_requirements` and
`submit_requirement_score` functions run against in-memory doubles.
`SemanticSearchService.get_rubric` runs against an in-memory store.
Regulation search, bid search, portfolio search, web search, and the three
models are fakes. FastMCP transport and SQL stay outside.

A model reports a 9 only when its requirement, the tender, the whole rubric,
and both evidence texts arrived. The venue model reports a breach otherwise,
and that score would be 4.

T-AG-20 — VENUE (threshold, score 9), CONTENT, and EXPERIENCE are each submitted.
T-AG-8 — that threshold scores 4, and the later two models are not assessed.
T-AG-35 — VENUE, CONTENT, HOST, MUSIC, and EXPERIENCE are each submitted.
"""

from __future__ import annotations

from app.commands.score_requirement import ScoreRequirement
from app.infrastructure.vector_store import RUBRICS, Match
from app.services.semantic_search_service import SemanticSearchService
from agent.orchestrator import score_bid
from agent.subagents.content import ContentAssessment, ContentMaterial, Verification as ContentVerification
from agent.subagents.host import HostAssessment, HostMaterial
from agent.subagents.music import MusicAssessment, MusicMaterial
from agent.subagents.experience import (
    ExperienceAssessment,
    ExperienceMaterial,
    Verification as ExperienceVerification,
)
from agent.subagents.venue import VenueAssessment, VenueMaterial, Verification, WebDocument
from mcp_server.server import get_tender_requirements, submit_requirement_score

_SUPPLIER = "sup-aurora"
_BID = "bid-1"
_TENDER_ID = "tender-1"
_TENDER_NAME = "טקס פרסי התרבות"
_REGION = "תל אביב"
_EVENT_DATE = "2026-06-01"

_VENUE_ID = "req-venue"
_CONTENT_ID = "req-content"
_HOST_ID = "req-host"
_MUSIC_ID = "req-music"
_EXPERIENCE_ID = "req-experience"
_VENUE_ANSWER = "היכל התרבות, 8000 מושבים, שני פתחי יציאה ותיאום משטרה."
_CONTENT_ANSWER = "19:00 פתיחה, 20:00 מחווה ליוצרים ישראליים עם ריטה, 21:30 סיום."
_HOST_ANSWER = "המנחה: נועה קירל. גיבוי: דנה אינטרנשיונל."
_MUSIC_ANSWER = "הופעה חיה של עידן רייכל. ריידר: במה, הגברה וחשמל."
_EXPERIENCE_ANSWER = "הפקנו את בלומפילד 2023, פארק הירקון 2024 וחוצות תל אביב 2025."
_VENUE_DESCRIPTION = "אולם של 2000 מושבים לפחות"
_CONTENT_DESCRIPTION = "לוח תוכנית וקונספט לטקס"
_HOST_DESCRIPTION = "מנחה מן הרשימה המאושרת, ומנחה גיבוי"
_MUSIC_DESCRIPTION = "הופעה חיה וריידר טכני לבמה, הגברה וחשמל"
_EXPERIENCE_DESCRIPTION = "רקורד בהפקת אירועי חוץ או אצטדיון"
_APPROVED_HOSTS = ("נועה קירל", "דנה אינטרנשיונל")

_VENUE_RUBRIC = (
    "requirement_type: VENUE\n"
    'rubric_version: "3"\n'
    "### 9–10\n"
    "Official capacity verified and the plan meets every retrieved obligation.\n"
)
_CONTENT_RUBRIC = (
    "requirement_type: CONTENT\n"
    'rubric_version: "1"\n'
    "### 9–10\n"
    "Schedule and concept are both present and on-theme.\n"
)
_HOST_RUBRIC = (
    "requirement_type: HOST\n"
    'rubric_version: "4"\n'
    "### 9–10\n"
    "The primary host and the backup are both approved, with no same-date conflict.\n"
)
_MUSIC_RUBRIC = (
    "requirement_type: MUSIC\n"
    'rubric_version: "5"\n'
    "### 9–10\n"
    "The live performance is detailed, the acts are verified, and the rider is complete.\n"
)
_EXPERIENCE_RUBRIC = (
    "requirement_type: EXPERIENCE\n"
    'rubric_version: "2"\n'
    "### 9–10\n"
    "Three or more outdoor or stadium productions were independently verified.\n"
)
_VENUE_VERSION = "3"
_CONTENT_VERSION = "1"
_HOST_VERSION = "4"
_MUSIC_VERSION = "5"
_EXPERIENCE_VERSION = "2"

_REGULATION = "מעל 500 איש נדרשים לפחות שני פתחי יציאה נפרדים."
_CONCEPT = "הצעה זוכה קודמת: לוח מלא וקונספט של מחווה ליוצרים ישראליים."
_MUSIC_CONCEPT = "הצעה זוכה קודמת: הופעה חיה עם ריידר לבמה, הגברה וחשמל."
_PORTFOLIO = "אורורה הפיקה את בלומפילד, את פארק הירקון ואת חוצות תל אביב."
_PAGE_URL = "https://evidence.example/official"
_PAGE_TEXT = "קיבולת רשמית: 8000. ריטה פעילה. שלושת אירועי החוץ אומתו. נועה קירל ודנה אינטרנשיונל פנויות. עידן רייכל פעיל."
_VENUE_JUSTIFICATION = "Official capacity is 8000 and the plan meets the retrieved rules."
_CONTENT_JUSTIFICATION = "The schedule and concept are on-theme, and Rita is independently verified."
_HOST_JUSTIFICATION = "Noa Kirel and Dana International are approved, with no same-date conflict."
_MUSIC_JUSTIFICATION = "Idan Raichel is active, the set is live, and the rider covers stage, sound, and power."
_EXPERIENCE_JUSTIFICATION = "Three outdoor productions were independently verified."

_RUBRICS = {
    "rubric:VENUE": (_VENUE_RUBRIC, _VENUE_VERSION, "VENUE"),
    "rubric:CONTENT": (_CONTENT_RUBRIC, _CONTENT_VERSION, "CONTENT"),
    "rubric:HOST": (_HOST_RUBRIC, _HOST_VERSION, "HOST"),
    "rubric:MUSIC": (_MUSIC_RUBRIC, _MUSIC_VERSION, "MUSIC"),
    "rubric:EXPERIENCE": (_EXPERIENCE_RUBRIC, _EXPERIENCE_VERSION, "EXPERIENCE"),
}


class _Requirements:
    def __init__(self, views: dict[str, dict]) -> None:
        self._views = views

    def fetch(self, tender_id: str) -> dict | None:
        return self._views.get(tender_id)


class _Scores:
    def __init__(self) -> None:
        self.commands: list[ScoreRequirement] = []

    def handle(self, command: ScoreRequirement) -> dict:
        self.commands.append(command)
        return {"accepted": True, "event_version": len(self.commands)}


class _RubricStore:
    """Key lookup only. A semantic query is not how a rubric is loaded."""

    def get(self, collection: str, ids: list[str]) -> list[Match]:
        if collection != RUBRICS:
            return []
        found = []
        for doc_id in ids:
            stored = _RUBRICS.get(doc_id)
            if stored is None:
                continue
            text, version, kind = stored
            found.append(
                Match(
                    id=doc_id,
                    document=text,
                    metadata={"requirement_type": kind, "rubric_version": version},
                    distance=0.0,
                )
            )
        return found

    def query(self, collection: str, text: str, k: int, where: dict | None = None) -> list[Match]:
        raise AssertionError("a rubric is loaded by id")

    def upsert(self, collection: str, ids: list[str], documents: list[str], metadata: list[dict]) -> None:
        raise AssertionError("the test store is read-only")


class _Search:
    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]:
        return [{"id": "reg-exits", "text": _REGULATION, "metadata": {}, "score": 1.0}]

    def search_bids(self, query: str, tender_id: str | None = None, top_k: int = 5) -> list[dict]:
        if tender_id is not None:
            return []
        return [
            {"id": "concept-prior", "text": _CONCEPT, "metadata": {}, "score": 1.0},
            {"id": "concept-music", "text": _MUSIC_CONCEPT, "metadata": {}, "score": 1.0},
        ]

    def search_portfolios(self, query: str, supplier_id: str, top_k: int = 5) -> list[dict]:
        if supplier_id != _SUPPLIER:
            return []
        return [
            {
                "id": "portfolio-aurora",
                "text": _PORTFOLIO,
                "metadata": {"supplier_id": supplier_id},
                "score": 1.0,
            }
        ]


class _Web:
    def search(self, query: str) -> tuple[WebDocument, ...]:
        return (WebDocument(url=_PAGE_URL, title="Evidence", content=_PAGE_TEXT),)


def _tender_matches(tender: object) -> bool:
    return (
        getattr(tender, "name") == _TENDER_NAME
        and getattr(tender, "event_date") == _EVENT_DATE
        and getattr(tender, "region") == _REGION
    )


class _VenueModel:
    def __init__(self, *, breached: bool = False) -> None:
        self.calls = 0
        self._breached = breached

    def assess(self, material: VenueMaterial) -> VenueAssessment:
        self.calls += 1
        ready = (
            material.answer == _VENUE_ANSWER
            and material.requirement.id == _VENUE_ID
            and material.requirement.description == _VENUE_DESCRIPTION
            and material.rubric_text == _VENUE_RUBRIC
            and _tender_matches(material.tender)
            and _REGULATION in "\n".join(material.regulations)
            and _PAGE_TEXT in "\n".join(document.content for document in material.web_results)
        )
        if ready == self._breached:
            return VenueAssessment(
                proposed_score=9,
                justification="A retrieved obligation is breached.",
                sources=(_PAGE_URL,),
                verifications=(),
                capacity_claim="verified",
                meets_tender_capacity=True,
                regulation_breached=True,
            )
        return VenueAssessment(
            proposed_score=9,
            justification=_VENUE_JUSTIFICATION,
            sources=(_PAGE_URL,),
            verifications=(
                Verification(claim=_VENUE_JUSTIFICATION, status="verified", source=_PAGE_URL),
            ),
            capacity_claim="verified",
            meets_tender_capacity=True,
            regulation_breached=False,
        )


class _ContentModel:
    def __init__(self) -> None:
        self.calls = 0

    def assess(self, material: ContentMaterial) -> ContentAssessment:
        self.calls += 1
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
                proposed_score=4,
                justification="The program was not assessed.",
                sources=(_PAGE_URL,),
                verifications=(),
                performer_claim="inconclusive",
                both_components_sound=False,
                substantive_defect=False,
            )
        return ContentAssessment(
            proposed_score=9,
            justification=_CONTENT_JUSTIFICATION,
            sources=(_PAGE_URL,),
            verifications=(
                ContentVerification(
                    claim=_CONTENT_JUSTIFICATION, status="verified", source=_PAGE_URL
                ),
            ),
            performer_claim="verified",
            both_components_sound=True,
            substantive_defect=False,
        )


class _ExperienceModel:
    def __init__(self) -> None:
        self.calls = 0

    def assess(self, material: ExperienceMaterial) -> ExperienceAssessment:
        self.calls += 1
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
                proposed_score=4,
                justification="The track record was not assessed.",
                sources=(_PAGE_URL,),
                verifications=(),
                any_disproved=False,
                verified_outdoor_count=0,
                outdoor_claimed=False,
            )
        return ExperienceAssessment(
            proposed_score=9,
            justification=_EXPERIENCE_JUSTIFICATION,
            sources=(_PAGE_URL,),
            verifications=(
                ExperienceVerification(
                    claim=_EXPERIENCE_JUSTIFICATION, status="verified", source=_PAGE_URL
                ),
            ),
            any_disproved=False,
            verified_outdoor_count=3,
            outdoor_claimed=True,
        )


class _HostModel:
    def __init__(self) -> None:
        self.calls = 0

    def assess(self, material: HostMaterial) -> HostAssessment:
        self.calls += 1
        ready = (
            material.answer == _HOST_ANSWER
            and material.requirement.id == _HOST_ID
            and material.requirement.description == _HOST_DESCRIPTION
            and material.rubric_text == _HOST_RUBRIC
            and _tender_matches(material.tender)
            and tuple(material.approved_hosts) == _APPROVED_HOSTS
            and _PAGE_TEXT in "\n".join(document.content for document in material.web_results)
        )
        if not ready:
            return HostAssessment(
                proposed_score=4,
                justification="The host was not assessed.",
                sources=(_PAGE_URL,),
                verifications=(),
                host_name="",
                backup_name="",
                primary_conflict=False,
                backup_conflict=False,
            )
        return HostAssessment(
            proposed_score=9,
            justification=_HOST_JUSTIFICATION,
            sources=(_PAGE_URL,),
            verifications=(),
            host_name="נועה קירל",
            backup_name="דנה אינטרנשיונל",
            primary_conflict=False,
            backup_conflict=False,
        )


class _MusicModel:
    def __init__(self) -> None:
        self.calls = 0

    def assess(self, material: MusicMaterial) -> MusicAssessment:
        self.calls += 1
        ready = (
            material.answer == _MUSIC_ANSWER
            and material.requirement.id == _MUSIC_ID
            and material.requirement.description == _MUSIC_DESCRIPTION
            and material.rubric_text == _MUSIC_RUBRIC
            and _tender_matches(material.tender)
            and _MUSIC_CONCEPT in "\n".join(material.concepts)
            and _PAGE_TEXT in "\n".join(document.content for document in material.web_results)
        )
        if not ready:
            return MusicAssessment(
                proposed_score=4,
                justification="The music plan was not assessed.",
                sources=(_PAGE_URL,),
                verifications=(),
                act_claim="inconclusive",
                live_offered=False,
                tender_requires_live=False,
                rider_complete=False,
            )
        return MusicAssessment(
            proposed_score=9,
            justification=_MUSIC_JUSTIFICATION,
            sources=(_PAGE_URL,),
            verifications=(),
            act_claim="verified",
            live_offered=True,
            tender_requires_live=True,
            rider_complete=True,
        )


class _UnusedModel:
    """A stand-in for a type this tender does not ask to score."""

    def assess(self, material: object) -> None:
        raise AssertionError("this tender does not dispatch this model")


def _view() -> dict[str, dict]:
    return {
        _TENDER_ID: {
            "tender": {
                "name": _TENDER_NAME,
                "type": "culture",
                "event_date": _EVENT_DATE,
                "region": _REGION,
                "alpha": 0.6,
            },
            "requirements": [
                {
                    "id": _VENUE_ID,
                    "type": "VENUE",
                    "weight": 40,
                    "is_threshold": True,
                    "description": _VENUE_DESCRIPTION,
                },
                {
                    "id": _CONTENT_ID,
                    "type": "CONTENT",
                    "weight": 30,
                    "is_threshold": False,
                    "description": _CONTENT_DESCRIPTION,
                },
                {
                    "id": _EXPERIENCE_ID,
                    "type": "EXPERIENCE",
                    "weight": 30,
                    "is_threshold": False,
                    "description": _EXPERIENCE_DESCRIPTION,
                },
            ],
        }
    }


def _score(venue_model: _VenueModel, content_model: _ContentModel, experience_model: _ExperienceModel) -> _Scores:
    scores = _Scores()
    score_bid(
        _BID,
        _TENDER_ID,
        _SUPPLIER,
        {
            _VENUE_ID: _VENUE_ANSWER,
            _CONTENT_ID: _CONTENT_ANSWER,
            _EXPERIENCE_ID: _EXPERIENCE_ANSWER,
        },
        requirements=lambda tender_id: get_tender_requirements(tender_id, _Requirements(_view())),
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
    )
    return scores


def _command(requirement_id: str, score: int, justification: str, version: str) -> ScoreRequirement:
    return ScoreRequirement(
        bid_id=_BID,
        requirement_id=requirement_id,
        score=score,
        justification=justification,
        sources=(_PAGE_URL,),
        rubric_version=version,
        scored_by="agent",
    )


def test_score_bid_submits_venue_content_and_experience() -> None:
    """T-AG-20. The VENUE threshold scores 9, so the later two are still submitted."""
    scores = _score(_VenueModel(), _ContentModel(), _ExperienceModel())

    assert scores.commands == [
        _command(_VENUE_ID, 9, _VENUE_JUSTIFICATION, _VENUE_VERSION),
        _command(_CONTENT_ID, 9, _CONTENT_JUSTIFICATION, _CONTENT_VERSION),
        _command(_EXPERIENCE_ID, 9, _EXPERIENCE_JUSTIFICATION, _EXPERIENCE_VERSION),
    ]


def test_threshold_below_six_stops_dispatch() -> None:
    """T-AG-8. VENUE scores 4. CONTENT and EXPERIENCE are not assessed."""
    content = _ContentModel()
    experience = _ExperienceModel()
    scores = _score(_VenueModel(breached=True), content, experience)

    assert content.calls == 0
    assert experience.calls == 0
    assert scores.commands == [
        _command(_VENUE_ID, 4, "A retrieved obligation is breached.", _VENUE_VERSION),
    ]


def _five_view() -> dict[str, dict]:
    return {
        _TENDER_ID: {
            "tender": {
                "name": _TENDER_NAME,
                "type": "culture",
                "event_date": _EVENT_DATE,
                "region": _REGION,
                "alpha": 0.6,
            },
            "approved_hosts": list(_APPROVED_HOSTS),
            "requirements": [
                {
                    "id": _VENUE_ID,
                    "type": "VENUE",
                    "weight": 25,
                    "is_threshold": True,
                    "description": _VENUE_DESCRIPTION,
                },
                {
                    "id": _CONTENT_ID,
                    "type": "CONTENT",
                    "weight": 25,
                    "is_threshold": False,
                    "description": _CONTENT_DESCRIPTION,
                },
                {
                    "id": _HOST_ID,
                    "type": "HOST",
                    "weight": 15,
                    "is_threshold": False,
                    "description": _HOST_DESCRIPTION,
                },
                {
                    "id": _MUSIC_ID,
                    "type": "MUSIC",
                    "weight": 15,
                    "is_threshold": False,
                    "description": _MUSIC_DESCRIPTION,
                },
                {
                    "id": _EXPERIENCE_ID,
                    "type": "EXPERIENCE",
                    "weight": 20,
                    "is_threshold": False,
                    "description": _EXPERIENCE_DESCRIPTION,
                },
            ],
        }
    }


def test_score_bid_submits_all_five_requirement_types() -> None:
    """T-AG-35. The VENUE threshold scores 9, so HOST and MUSIC are scored too."""
    scores = _Scores()
    score_bid(
        _BID,
        _TENDER_ID,
        _SUPPLIER,
        {
            _VENUE_ID: _VENUE_ANSWER,
            _CONTENT_ID: _CONTENT_ANSWER,
            _HOST_ID: _HOST_ANSWER,
            _MUSIC_ID: _MUSIC_ANSWER,
            _EXPERIENCE_ID: _EXPERIENCE_ANSWER,
        },
        requirements=lambda tender_id: get_tender_requirements(
            tender_id, _Requirements(_five_view())
        ),
        rubrics=SemanticSearchService(_RubricStore()),
        search=_Search(),
        web=_Web(),
        venue_model=_VenueModel(),
        content_model=_ContentModel(),
        experience_model=_ExperienceModel(),
        host_model=_HostModel(),
        music_model=_MusicModel(),
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
    )

    assert scores.commands == [
        _command(_VENUE_ID, 9, _VENUE_JUSTIFICATION, _VENUE_VERSION),
        _command(_CONTENT_ID, 9, _CONTENT_JUSTIFICATION, _CONTENT_VERSION),
        _command(_HOST_ID, 9, _HOST_JUSTIFICATION, _HOST_VERSION),
        _command(_MUSIC_ID, 9, _MUSIC_JUSTIFICATION, _MUSIC_VERSION),
        _command(_EXPERIENCE_ID, 9, _EXPERIENCE_JUSTIFICATION, _EXPERIENCE_VERSION),
    ]
