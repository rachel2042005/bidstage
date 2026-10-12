"""End-to-end interactive demo of the tender evaluation pipeline.

The doubles speak the same contracts as `run_once`: a requirements payload,
`get_rubric`, search hits shaped `{text, ...}`, web pages with `content`,
and the three assessment dataclasses. Scores are still proposed by stand-ins,
so the run does not call a model or the network.
"""

from agent.runner import AssessmentWork, run_once
from agent.subagents.content import ContentAssessment
from agent.subagents.experience import ExperienceAssessment
from agent.subagents.venue import VenueAssessment

tender_id = "tender-fest-2026"
bid_id = "bid-supplier-alpha"
supplier_id = "sup-101"
_PAGE = "https://official-records.example/festival"

requirements = [
    {
        "id": "req-venue",
        "type": "VENUE",
        "weight": 40,
        "is_threshold": True,
        "description": "אולם או שטח של 10000 מושבים לפחות",
    },
    {
        "id": "req-content",
        "type": "CONTENT",
        "weight": 30,
        "is_threshold": False,
        "description": "לוח תוכנית וקונספט לפסטיבל",
    },
    {
        "id": "req-exp",
        "type": "EXPERIENCE",
        "weight": 30,
        "is_threshold": False,
        "description": "רקורד בהפקת אירועי חוץ",
    },
]

supplier_answers = {
    "req-venue": "We offer Park HaYarkon main lawn with verified capacity for 15,000 attendees and safety permits.",
    "req-content": "A detailed 8-hour symphonic program featuring verified orchestral performers and full timeline.",
    "req-exp": "Produced 4 major outdoor festivals over the last 3 years, each exceeding 10,000 attendees.",
}


class DemoQueue:
    def __init__(self) -> None:
        self.served = False

    def claim(self) -> AssessmentWork | None:
        if self.served:
            return None
        self.served = True
        return AssessmentWork(
            bid_id=bid_id,
            tender_id=tender_id,
            supplier_id=supplier_id,
            answers=supplier_answers,
        )


def get_requirements(_tender_id: str) -> dict:
    return {
        "tender": {
            "name": "Summer Festival",
            "event_date": "2026-07-15",
            "region": "Tel Aviv",
        },
        "requirements": requirements,
    }


class DemoRubrics:
    def get_rubric(self, requirement_type: str) -> dict:
        return {
            "text": f"Rubric {requirement_type} text",
            "metadata": {"rubric_version": "1"},
        }


class DemoSearch:
    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]:
        return [{"id": "reg-1", "text": "Outdoor capacity permits are valid.", "metadata": {}, "score": 1.0}]

    def search_bids(self, query: str, tender_id: str | None = None, top_k: int = 5) -> list[dict]:
        return [{"id": "concept-1", "text": "Past festival concept guidelines.", "metadata": {}, "score": 1.0}]

    def search_portfolios(self, query: str, supplier_id: str, top_k: int = 5) -> list[dict]:
        return [{"id": "portfolio-1", "text": "Verified stadium shows.", "metadata": {}, "score": 1.0}]


class DemoPage:
    def __init__(self) -> None:
        self.url = _PAGE
        self.title = "Official record"
        self.content = "Public registry corroborates capacity and dates."


class DemoWeb:
    def search(self, query: str) -> tuple[DemoPage, ...]:
        return (DemoPage(),)


_JUSTIFICATION = "All requirements fully satisfied and verified independently."


class VenueDemo:
    def assess(self, material: object) -> VenueAssessment:
        return VenueAssessment(
            proposed_score=9,
            justification=_JUSTIFICATION,
            sources=(_PAGE,),
            verifications=(),
            capacity_claim="verified",
            meets_tender_capacity=True,
            regulation_breached=False,
        )


class ContentDemo:
    def assess(self, material: object) -> ContentAssessment:
        return ContentAssessment(
            proposed_score=9,
            justification=_JUSTIFICATION,
            sources=(_PAGE,),
            verifications=(),
            performer_claim="verified",
            both_components_sound=True,
            substantive_defect=False,
        )


class ExperienceDemo:
    def assess(self, material: object) -> ExperienceAssessment:
        return ExperienceAssessment(
            proposed_score=9,
            justification=_JUSTIFICATION,
            sources=(_PAGE,),
            verifications=(),
            any_disproved=False,
            verified_outdoor_count=3,
            outdoor_claimed=True,
        )


submitted_scores: list[tuple[str, int]] = []
terminal_events: list[object] = []


def record_score(
    bid_id: str,
    requirement_id: str,
    score: int,
    justification: str,
    sources: list[str],
    rubric_version: str,
) -> dict:
    print("\n[MCP Command] -> submit_requirement_score:")
    print(f"   Requirement: {requirement_id}")
    print(f"   Integer Score: {score}/10")
    print(f"   Justification: {justification}")
    print(f"   Sources: {sources}")
    print(f"   Rubric version: {rubric_version}")
    submitted_scores.append((requirement_id, score))
    return {"accepted": True, "event_version": len(submitted_scores)}


def record_terminal(command: object) -> None:
    payload = {
        key: value
        for key, value in vars(command).items()
        if value is not None
    }
    print(f"\n{'=' * 50}")
    print(f"[Terminal Event] -> {type(command).__name__}:")
    for key, value in payload.items():
        print(f"   {key}: {value}")
    print(f"{'=' * 50}")
    terminal_events.append(command)


print("\n--- מתחילים תהליך הערכה מלא של הצעה (End-to-End Demo) ---")

assessed = run_once(
    DemoQueue(),
    requirements=get_requirements,
    rubrics=DemoRubrics(),
    search=DemoSearch(),
    web=DemoWeb(),
    venue_model=VenueDemo(),
    content_model=ContentDemo(),
    experience_model=ExperienceDemo(),
    submit=record_score,
    complete=record_terminal,
)

print(f"\nBids assessed this cycle: {assessed}")
