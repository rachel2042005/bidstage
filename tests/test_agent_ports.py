"""Network ports for the agent (`NFR-MCP-1`, `NFR-AGT-3`).

Seams: `TavilyPageSearch.search`, `StructuredAssessor.assess_*` and `assess`,
`load_settings` for `AGENT_MODEL_PROVIDER`, and `build_runtime`. No Tavily,
OpenAI, Ollama, SQL Server, or Chroma call.

T-AG-45 — an empty key searches nothing.
T-AG-46 — one Tavily hit becomes url, title, and content.
T-AG-47 — venue JSON with proposed_score 4 comes back at temperature 0.
T-AG-48 — content JSON with proposed_score 4 comes back at temperature 0.
T-AG-49 — host JSON with proposed_score 4 comes back at temperature 0.
T-AG-50 — music JSON with proposed_score 4 comes back at temperature 0.
T-AG-51 — experience JSON with proposed_score 4 comes back at temperature 0.
T-AG-52 — `assess` dispatches on the material type.
T-AG-53 — `AGENT_MODEL_PROVIDER` selects the chat model.
T-AG-54 — `build_runtime` wires search, web, and one assessor.
T-AG-55 — omitted requirements and submit are the SQL wrappers.
"""

from __future__ import annotations

import json

import pytest

from agent.runner import _sql_requirements, _sql_submit, build_runtime
from app.config import ConfigError, load_settings
from app.services.semantic_search_service import SemanticSearchService
from agent.adapters.assessor import StructuredAssessor
from agent.subagents.experience import (
    ExperienceAssessment,
    ExperienceMaterial,
    ExperienceRequirement,
    TenderContext as ExperienceTender,
    Verification as ExperienceVerification,
)
from agent.subagents.host import (
    HostAssessment,
    HostMaterial,
    HostRequirement,
    TenderContext as HostTender,
    Verification as HostVerification,
)
from agent.subagents.music import (
    MusicAssessment,
    MusicMaterial,
    MusicRequirement,
    TenderContext as MusicTender,
    Verification as MusicVerification,
)
from agent.subagents.content import (
    ContentAssessment,
    ContentMaterial,
    ContentRequirement,
    TenderContext as ContentTender,
    Verification as ContentVerification,
)
from agent.adapters.tavily import TavilyPageSearch, WebPage
from agent.subagents.venue import (
    TenderContext,
    VenueAssessment,
    VenueMaterial,
    VenueRequirement,
    Verification,
)


def test_empty_tavily_key_searches_nothing() -> None:
    """T-AG-45. An empty key returns no pages and does not call Tavily."""
    calls: list[str] = []

    def post(api_key: str, query: str) -> dict:
        calls.append(query)
        return {"results": []}

    pages = TavilyPageSearch("", post=post).search("היכל")

    assert pages == ()
    assert calls == []


def test_tavily_hit_becomes_url_title_and_content() -> None:
    """T-AG-46. One hit for היכל is returned as those three fields."""

    def post(api_key: str, query: str) -> dict:
        assert api_key == "tvly-test"
        assert query == "היכל"
        return {
            "results": [
                {
                    "url": "https://example.com/hall",
                    "title": "Hall",
                    "content": "5000 seats",
                    "score": 0.9,
                }
            ]
        }

    pages = TavilyPageSearch("tvly-test", post=post).search("היכל")

    assert pages == (WebPage("https://example.com/hall", "Hall", "5000 seats"),)


def test_tavily_posts_the_query_and_key() -> None:
    """T-AG-46. The live post is POST https://api.tavily.com/search."""
    seen: dict[str, object] = {}

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def read(self) -> bytes:
            return (
                b'{"results":[{"url":"https://example.com/hall",'
                b'"title":"Hall","content":"5000 seats"}]}'
            )

    def opener(request: object, timeout: float) -> _Response:
        seen["url"] = request.full_url  # type: ignore[attr-defined]
        seen["method"] = request.method  # type: ignore[attr-defined]
        seen["body"] = request.data.decode("utf-8")  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        return _Response()

    pages = TavilyPageSearch("tvly-test", opener=opener).search("היכל")
    body = json.loads(str(seen["body"]))

    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["method"] == "POST"
    assert body["api_key"] == "tvly-test"
    assert body["query"] == "היכל"
    assert pages == (WebPage("https://example.com/hall", "Hall", "5000 seats"),)


_VENUE_JSON = {
    "proposed_score": 4,
    "justification": "Capacity is unverified.",
    "sources": ["https://example.com/hall"],
    "verifications": [
        {
            "claim": "Hall seats 5000",
            "status": "inconclusive",
            "source": "https://example.com/hall",
        }
    ],
    "capacity_claim": "inconclusive",
    "meets_tender_capacity": False,
    "regulation_breached": False,
}

_VENUE = VenueAssessment(
    proposed_score=4,
    justification="Capacity is unverified.",
    sources=("https://example.com/hall",),
    verifications=(
        Verification(
            claim="Hall seats 5000",
            status="inconclusive",
            source="https://example.com/hall",
        ),
    ),
    capacity_claim="inconclusive",
    meets_tender_capacity=False,
    regulation_breached=False,
)


def _venue_material() -> VenueMaterial:
    return VenueMaterial(
        requirement=VenueRequirement(id="req-venue", description="אולם"),
        answer="היכל",
        rubric_text="First match: disproved capacity scores 0.",
        tender=TenderContext(name="טקס", event_date="2026-06-01", region="צפון"),
        regulations=("capacity limit",),
        web_results=(),
    )


def test_venue_assessment_parses_at_temperature_zero() -> None:
    """T-AG-47. Score 4 is returned. 11 and true are rejected."""
    seen: dict[str, object] = {}

    def complete(*, temperature: int, schema_name: str, user: str) -> str:
        seen["temperature"] = temperature
        seen["schema_name"] = schema_name
        assert "First match: disproved capacity scores 0." in user
        return json.dumps(_VENUE_JSON)

    assessment = StructuredAssessor(complete=complete).assess_venue(_venue_material())

    assert seen["temperature"] == 0
    assert seen["schema_name"] == "VenueAssessment"
    assert assessment == _VENUE

    def reject(score: object) -> str:
        payload = dict(_VENUE_JSON)
        payload["proposed_score"] = score
        return json.dumps(payload)

    def complete_eleven(*, temperature: int, schema_name: str, user: str) -> str:
        return reject(11)

    def complete_true(*, temperature: int, schema_name: str, user: str) -> str:
        return reject(True)

    with pytest.raises(ValueError):
        StructuredAssessor(complete=complete_eleven).assess_venue(_venue_material())
    with pytest.raises(ValueError):
        StructuredAssessor(complete=complete_true).assess_venue(_venue_material())


_CONTENT_JSON = {
    "proposed_score": 4,
    "justification": "The schedule contradicts the concept.",
    "sources": ["https://example.com/act"],
    "verifications": [
        {
            "claim": "The ensemble is touring",
            "status": "inconclusive",
            "source": "https://example.com/act",
        }
    ],
    "performer_claim": "inconclusive",
    "both_components_sound": False,
    "substantive_defect": True,
}


def test_content_assessment_parses_at_temperature_zero() -> None:
    """T-AG-48. Score 4 is returned with the content findings."""
    seen: dict[str, object] = {}

    def complete(*, temperature: int, schema_name: str, user: str) -> str:
        seen["temperature"] = temperature
        seen["schema_name"] = schema_name
        assert "Content rubric pins a contradicted schedule to 3-4." in user
        return json.dumps(_CONTENT_JSON)

    material = ContentMaterial(
        requirement=ContentRequirement(id="req-content", description="תוכן"),
        answer="מופע",
        rubric_text="Content rubric pins a contradicted schedule to 3-4.",
        tender=ContentTender(name="טקס", event_date="2026-06-01", region="צפון"),
        concepts=("prior opening",),
        web_results=(),
    )
    assessment = StructuredAssessor(complete=complete).assess_content(material)

    assert seen["temperature"] == 0
    assert seen["schema_name"] == "ContentAssessment"
    assert assessment == ContentAssessment(
        proposed_score=4,
        justification="The schedule contradicts the concept.",
        sources=("https://example.com/act",),
        verifications=(
            ContentVerification(
                claim="The ensemble is touring",
                status="inconclusive",
                source="https://example.com/act",
            ),
        ),
        performer_claim="inconclusive",
        both_components_sound=False,
        substantive_defect=True,
    )


def _complete(rubric: str, payload: dict) -> tuple[dict, object]:
    seen: dict[str, object] = {}

    def complete(*, temperature: int, schema_name: str, user: str) -> str:
        seen["temperature"] = temperature
        seen["schema_name"] = schema_name
        assert rubric in user
        return json.dumps(payload)

    return seen, complete


def test_host_assessment_parses_at_temperature_zero() -> None:
    """T-AG-49. Score 4 is returned with the host findings."""
    payload = {
        "proposed_score": 4,
        "justification": "The primary host is booked that night.",
        "sources": ["https://example.com/host"],
        "verifications": [
            {
                "claim": "Same-date engagement",
                "status": "verified",
                "source": "https://example.com/host",
            }
        ],
        "host_name": "דנה",
        "backup_name": "יוסי",
        "primary_conflict": True,
        "backup_conflict": False,
    }
    seen, complete = _complete(
        "Host rubric pins a listed host with a conflict to 3-4.",
        payload,
    )
    material = HostMaterial(
        requirement=HostRequirement(id="req-host", description="מנחה"),
        answer="דנה",
        rubric_text="Host rubric pins a listed host with a conflict to 3-4.",
        tender=HostTender(name="טקס", event_date="2026-06-01", region="צפון"),
        approved_hosts=("דנה", "יוסי"),
        web_results=(),
    )
    assessment = StructuredAssessor(complete=complete).assess_host(material)

    assert seen["temperature"] == 0
    assert seen["schema_name"] == "HostAssessment"
    assert assessment == HostAssessment(
        proposed_score=4,
        justification="The primary host is booked that night.",
        sources=("https://example.com/host",),
        verifications=(
            HostVerification(
                claim="Same-date engagement",
                status="verified",
                source="https://example.com/host",
            ),
        ),
        host_name="דנה",
        backup_name="יוסי",
        primary_conflict=True,
        backup_conflict=False,
    )


def test_music_assessment_parses_at_temperature_zero() -> None:
    """T-AG-50. Score 4 is returned with the music findings."""
    payload = {
        "proposed_score": 4,
        "justification": "The named act could not be confirmed.",
        "sources": ["https://example.com/act"],
        "verifications": [
            {
                "claim": "The band is active",
                "status": "inconclusive",
                "source": "https://example.com/act",
            }
        ],
        "act_claim": "inconclusive",
        "live_offered": True,
        "tender_requires_live": True,
        "rider_complete": False,
    }
    seen, complete = _complete(
        "Music rubric pins an incomplete rider to 3-4.",
        payload,
    )
    material = MusicMaterial(
        requirement=MusicRequirement(id="req-music", description="מוזיקה"),
        answer="להקה",
        rubric_text="Music rubric pins an incomplete rider to 3-4.",
        tender=MusicTender(name="טקס", event_date="2026-06-01", region="צפון"),
        concepts=("live set",),
        web_results=(),
    )
    assessment = StructuredAssessor(complete=complete).assess_music(material)

    assert seen["temperature"] == 0
    assert seen["schema_name"] == "MusicAssessment"
    assert assessment == MusicAssessment(
        proposed_score=4,
        justification="The named act could not be confirmed.",
        sources=("https://example.com/act",),
        verifications=(
            MusicVerification(
                claim="The band is active",
                status="inconclusive",
                source="https://example.com/act",
            ),
        ),
        act_claim="inconclusive",
        live_offered=True,
        tender_requires_live=True,
        rider_complete=False,
    )


def test_experience_assessment_parses_at_temperature_zero() -> None:
    """T-AG-51. Score 4 is returned with three verified outdoor shows."""
    payload = {
        "proposed_score": 4,
        "justification": "Three outdoor shows are verified.",
        "sources": ["https://example.com/show"],
        "verifications": [
            {
                "claim": "Stadium show in 2024",
                "status": "verified",
                "source": "https://example.com/show",
            }
        ],
        "any_disproved": False,
        "verified_outdoor_count": 3,
        "outdoor_claimed": True,
    }
    seen, complete = _complete(
        "Experience rubric pins three verified outdoor shows to 9-10.",
        payload,
    )
    material = ExperienceMaterial(
        requirement=ExperienceRequirement(id="req-experience", description="ניסיון"),
        answer="איצטדיון",
        rubric_text="Experience rubric pins three verified outdoor shows to 9-10.",
        tender=ExperienceTender(name="טקס", event_date="2026-06-01", region="צפון"),
        supplier_id="sup-1",
        portfolios=("stadium 2024",),
        web_results=(),
    )
    assessment = StructuredAssessor(complete=complete).assess_experience(material)

    assert seen["temperature"] == 0
    assert seen["schema_name"] == "ExperienceAssessment"
    assert assessment == ExperienceAssessment(
        proposed_score=4,
        justification="Three outdoor shows are verified.",
        sources=("https://example.com/show",),
        verifications=(
            ExperienceVerification(
                claim="Stadium show in 2024",
                status="verified",
                source="https://example.com/show",
            ),
        ),
        any_disproved=False,
        verified_outdoor_count=3,
        outdoor_claimed=True,
    )


def test_assess_dispatches_on_the_material_type() -> None:
    """T-AG-52. One assessor serves every material through `assess`."""
    payloads = {
        "VenueAssessment": _VENUE_JSON,
        "ContentAssessment": _CONTENT_JSON,
        "HostAssessment": {
            "proposed_score": 4,
            "justification": "The primary host is booked that night.",
            "sources": ["https://example.com/host"],
            "verifications": [],
            "host_name": "דנה",
            "backup_name": "",
            "primary_conflict": True,
            "backup_conflict": False,
        },
        "MusicAssessment": {
            "proposed_score": 4,
            "justification": "The named act could not be confirmed.",
            "sources": ["https://example.com/act"],
            "verifications": [],
            "act_claim": "inconclusive",
            "live_offered": True,
            "tender_requires_live": True,
            "rider_complete": False,
        },
        "ExperienceAssessment": {
            "proposed_score": 4,
            "justification": "Three outdoor shows are verified.",
            "sources": ["https://example.com/show"],
            "verifications": [],
            "any_disproved": False,
            "verified_outdoor_count": 3,
            "outdoor_claimed": True,
        },
    }
    seen: list[int] = []

    def complete(*, temperature: int, schema_name: str, user: str) -> str:
        seen.append(temperature)
        return json.dumps(payloads[schema_name])

    assessor = StructuredAssessor(complete=complete)
    content = ContentMaterial(
        requirement=ContentRequirement(id="req-content", description="תוכן"),
        answer="מופע",
        rubric_text="content",
        tender=ContentTender(name="טקס", event_date="2026-06-01", region="צפון"),
        concepts=(),
        web_results=(),
    )
    host = HostMaterial(
        requirement=HostRequirement(id="req-host", description="מנחה"),
        answer="דנה",
        rubric_text="host",
        tender=HostTender(name="טקס", event_date="2026-06-01", region="צפון"),
        approved_hosts=(),
        web_results=(),
    )
    music = MusicMaterial(
        requirement=MusicRequirement(id="req-music", description="מוזיקה"),
        answer="להקה",
        rubric_text="music",
        tender=MusicTender(name="טקס", event_date="2026-06-01", region="צפון"),
        concepts=(),
        web_results=(),
    )
    experience = ExperienceMaterial(
        requirement=ExperienceRequirement(id="req-experience", description="ניסיון"),
        answer="איצטדיון",
        rubric_text="experience",
        tender=ExperienceTender(name="טקס", event_date="2026-06-01", region="צפון"),
        supplier_id="sup-1",
        portfolios=(),
        web_results=(),
    )

    assert assessor.assess(_venue_material()) == _VENUE
    assert type(assessor.assess(content)) is ContentAssessment
    assert type(assessor.assess(host)) is HostAssessment
    assert type(assessor.assess(music)) is MusicAssessment
    assert type(assessor.assess(experience)) is ExperienceAssessment
    assert seen == [0, 0, 0, 0, 0]


def test_agent_model_provider_selects_the_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-AG-53. Ollama and OpenAI each keep their own chat model."""
    monkeypatch.setenv(
        "SOMEE_DB_CONNECTION_STRING",
        "Driver={ODBC Driver 17 for SQL Server};Server=localhost;",
    )
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    ollama = load_settings()
    assert ollama.agent_model_provider == "ollama"
    assert ollama.agent_model == "qwen3:8b"
    assert ollama.agent_model_base_url == "http://localhost:11434/v1"

    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "openai")
    openai = load_settings()
    assert openai.agent_model_provider == "openai"
    assert openai.agent_model == "gpt-4o-mini"
    assert openai.agent_model_base_url is None

    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("AGENT_MODEL", "gpt-4o-mini")
    with pytest.raises(ConfigError):
        load_settings()


def test_chat_completion_asks_for_temperature_zero() -> None:
    """T-AG-47. The provider call is temperature 0 against VenueAssessment."""
    seen: dict[str, object] = {}

    class _Message:
        content = json.dumps(_VENUE_JSON)

    class _Choice:
        message = _Message()

    class _Response:
        choices = (_Choice(),)

    class _Completions:
        def create(self, **kwargs: object) -> _Response:
            seen["create"] = kwargs
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    def factory(*, api_key: str, base_url: str | None) -> _Client:
        seen["api_key"] = api_key
        seen["base_url"] = base_url
        return _Client()

    assessment = StructuredAssessor(
        provider="openai",
        model="gpt-4o-mini",
        base_url=None,
        api_key="sk-test",
        client_factory=factory,
    ).assess_venue(_venue_material())
    create = seen["create"]
    assert isinstance(create, dict)

    assert assessment == _VENUE
    assert create["temperature"] == 0
    assert create["model"] == "gpt-4o-mini"
    assert create["response_format"]["json_schema"]["name"] == "VenueAssessment"
    assert seen["api_key"] == "sk-test"
    assert seen["base_url"] is None
    assert "First match: disproved capacity scores 0." in create["messages"][1]["content"]


def test_build_runtime_wires_search_web_and_one_assessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-AG-54. Search is the rubric service. An empty key searches nothing."""
    monkeypatch.setenv(
        "SOMEE_DB_CONNECTION_STRING",
        "Driver={ODBC Driver 17 for SQL Server};Server=localhost;",
    )
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "openai")
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    settings = load_settings()
    queue = object()
    rubrics = SemanticSearchService(object())

    def requirements(tender_id: str) -> dict:
        return {"tender_id": tender_id}

    def submit(**kwargs: object) -> dict:
        return kwargs

    runtime = build_runtime(
        settings,
        requirements=requirements,
        submit=submit,
        queue=queue,
        rubrics=rubrics,
    )

    assert runtime.queue is queue
    assert runtime.rubrics is rubrics
    assert runtime.search is rubrics
    assert runtime.requirements is requirements
    assert runtime.submit is submit
    assert runtime.web.search("היכל") == ()
    assert runtime.venue_model is runtime.content_model
    assert runtime.venue_model is runtime.host_model
    assert runtime.venue_model is runtime.music_model
    assert runtime.venue_model is runtime.experience_model
    assert isinstance(runtime.venue_model, StructuredAssessor)


def test_build_runtime_uses_sql_requirements_and_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-AG-55. Omitted requirements and submit are the SQL wrappers."""
    monkeypatch.setenv(
        "SOMEE_DB_CONNECTION_STRING",
        "Driver={ODBC Driver 17 for SQL Server};Server=localhost;",
    )
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "openai")
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    settings = load_settings()
    queue = object()
    rubrics = SemanticSearchService(object())

    runtime = build_runtime(settings, queue=queue, rubrics=rubrics)

    assert runtime.queue is queue
    assert runtime.rubrics is rubrics
    assert runtime.search is rubrics
    assert runtime.requirements is _sql_requirements
    assert runtime.submit is _sql_submit
    assert runtime.web.search("היכל") == ()
    assert isinstance(runtime.venue_model, StructuredAssessor)
