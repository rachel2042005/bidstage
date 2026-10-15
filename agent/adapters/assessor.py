"""One chat client, five assessment schemas (`NFR-AGT-3`, `T-AG-47`).

The evaluator pins the band. This module only parses the model's proposal.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agent.subagents.experience import (
    ExperienceAssessment,
    ExperienceMaterial,
    Verification as ExperienceVerification,
)
from agent.subagents.host import (
    HostAssessment,
    HostMaterial,
    Verification as HostVerification,
)
from agent.subagents.music import (
    MusicAssessment,
    MusicMaterial,
    Verification as MusicVerification,
)
from agent.subagents.content import (
    ContentAssessment,
    ContentMaterial,
    Verification as ContentVerification,
)
from agent.subagents.venue import (
    VenueAssessment,
    VenueMaterial,
    Verification as VenueVerification,
)

_CLAIMS = frozenset({"verified", "disproved", "inconclusive"})
_STATUS_SCHEMA = {
    "type": "string",
    "enum": ["verified", "disproved", "inconclusive"],
}
_BOOL_SCHEMA = {"type": "boolean"}
_VERIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim": {"type": "string"},
        "status": _STATUS_SCHEMA,
        "source": {"type": "string"},
    },
    "required": ["claim", "status", "source"],
}


def _schema(properties: dict[str, dict]) -> dict[str, Any]:
    fields = {
        "proposed_score": {"type": "integer"},
        "justification": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "verifications": {"type": "array", "items": _VERIFICATION_SCHEMA},
        **properties,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": fields,
        "required": list(fields),
    }


_SCHEMAS = {
    "VenueAssessment": _schema(
        {
            "capacity_claim": _STATUS_SCHEMA,
            "meets_tender_capacity": _BOOL_SCHEMA,
            "regulation_breached": _BOOL_SCHEMA,
        }
    ),
    "ContentAssessment": _schema(
        {
            "performer_claim": _STATUS_SCHEMA,
            "both_components_sound": _BOOL_SCHEMA,
            "substantive_defect": _BOOL_SCHEMA,
        }
    ),
    "HostAssessment": _schema(
        {
            "host_name": {"type": "string"},
            "backup_name": {"type": "string"},
            "primary_conflict": _BOOL_SCHEMA,
            "backup_conflict": _BOOL_SCHEMA,
        }
    ),
    "MusicAssessment": _schema(
        {
            "act_claim": _STATUS_SCHEMA,
            "live_offered": _BOOL_SCHEMA,
            "tender_requires_live": _BOOL_SCHEMA,
            "rider_complete": _BOOL_SCHEMA,
        }
    ),
    "ExperienceAssessment": _schema(
        {
            "any_disproved": _BOOL_SCHEMA,
            "verified_outdoor_count": {"type": "integer"},
            "outdoor_claimed": _BOOL_SCHEMA,
        }
    ),
}


def _openai_client(*, api_key: str, base_url: str | None) -> Any:
    from app import bootstrap

    bootstrap.init()
    from openai import OpenAI  # noqa: E402

    if not api_key:
        raise ValueError(
            "No API key for the OpenAI chat model. Set OPENAI_API_KEY, "
            "or set AGENT_MODEL_PROVIDER=ollama."
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def _plain(value: Any) -> Any:
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is not None:
        return {key: _plain(getattr(value, key)) for key in fields}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _score(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
        raise ValueError("proposed_score must be an integer from 0 to 10.")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    return value


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required.")
    return value


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _claim(value: object, name: str) -> str:
    if value not in _CLAIMS:
        raise ValueError(f"{name} must be verified, disproved, or inconclusive.")
    return str(value)


def _sources(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("sources must be a list of non-empty strings.")
    return tuple(value)


def _verifications(value: object, kind: type) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise ValueError("verifications must be a list.")
    found: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("verifications must be objects.")
        found.append(
            kind(
                claim=_text(item.get("claim"), "claim"),
                status=_claim(item.get("status"), "status"),
                source=_text(item.get("source"), "source"),
            )
        )
    return tuple(found)


class StructuredAssessor:
    """Structured-output chat. `complete` stands in for the provider in tests."""

    def __init__(
        self,
        *,
        complete: Callable[..., str] | None = None,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        api_key: str = "",
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._complete = complete
        self._provider = provider
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._client_factory = client_factory or _openai_client
        self._client: Any = None

    def _parse(self, schema_name: str, material: Any) -> dict[str, Any]:
        user = json.dumps(_plain(material), ensure_ascii=False)
        if self._complete is None:
            raw = self._live(schema_name, user)
        else:
            raw = self._complete(temperature=0, schema_name=schema_name, user=user)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"{schema_name} must be an object.")
        return data

    def _live(self, schema_name: str, user: str) -> str:
        if self._client is None:
            self._client = self._client_factory(
                api_key=self._api_key, base_url=self._base_url
            )
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return one JSON object matching the schema. "
                        "Do not apply scoring bands."
                    ),
                },
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": _SCHEMAS[schema_name],
                },
            },
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{schema_name} returned no JSON.")
        return content

    def assess_venue(self, material: VenueMaterial) -> VenueAssessment:
        data = self._parse("VenueAssessment", material)
        return VenueAssessment(
            proposed_score=_score(data["proposed_score"]),
            justification=_text(data["justification"], "justification"),
            sources=_sources(data["sources"]),
            verifications=_verifications(data["verifications"], VenueVerification),
            capacity_claim=_claim(data["capacity_claim"], "capacity_claim"),  # type: ignore[arg-type]
            meets_tender_capacity=_flag(
                data["meets_tender_capacity"], "meets_tender_capacity"
            ),
            regulation_breached=_flag(data["regulation_breached"], "regulation_breached"),
        )

    def assess_content(self, material: ContentMaterial) -> ContentAssessment:
        data = self._parse("ContentAssessment", material)
        return ContentAssessment(
            proposed_score=_score(data["proposed_score"]),
            justification=_text(data["justification"], "justification"),
            sources=_sources(data["sources"]),
            verifications=_verifications(data["verifications"], ContentVerification),
            performer_claim=_claim(data["performer_claim"], "performer_claim"),  # type: ignore[arg-type]
            both_components_sound=_flag(
                data["both_components_sound"], "both_components_sound"
            ),
            substantive_defect=_flag(data["substantive_defect"], "substantive_defect"),
        )

    def assess_host(self, material: HostMaterial) -> HostAssessment:
        data = self._parse("HostAssessment", material)
        return HostAssessment(
            proposed_score=_score(data["proposed_score"]),
            justification=_text(data["justification"], "justification"),
            sources=_sources(data["sources"]),
            verifications=_verifications(data["verifications"], HostVerification),
            host_name=_string(data["host_name"], "host_name"),
            backup_name=_string(data["backup_name"], "backup_name"),
            primary_conflict=_flag(data["primary_conflict"], "primary_conflict"),
            backup_conflict=_flag(data["backup_conflict"], "backup_conflict"),
        )

    def assess_music(self, material: MusicMaterial) -> MusicAssessment:
        data = self._parse("MusicAssessment", material)
        return MusicAssessment(
            proposed_score=_score(data["proposed_score"]),
            justification=_text(data["justification"], "justification"),
            sources=_sources(data["sources"]),
            verifications=_verifications(data["verifications"], MusicVerification),
            act_claim=_claim(data["act_claim"], "act_claim"),  # type: ignore[arg-type]
            live_offered=_flag(data["live_offered"], "live_offered"),
            tender_requires_live=_flag(
                data["tender_requires_live"], "tender_requires_live"
            ),
            rider_complete=_flag(data["rider_complete"], "rider_complete"),
        )

    def assess_experience(self, material: ExperienceMaterial) -> ExperienceAssessment:
        data = self._parse("ExperienceAssessment", material)
        return ExperienceAssessment(
            proposed_score=_score(data["proposed_score"]),
            justification=_text(data["justification"], "justification"),
            sources=_sources(data["sources"]),
            verifications=_verifications(data["verifications"], ExperienceVerification),
            any_disproved=_flag(data["any_disproved"], "any_disproved"),
            verified_outdoor_count=_count(
                data["verified_outdoor_count"], "verified_outdoor_count"
            ),
            outdoor_claimed=_flag(data["outdoor_claimed"], "outdoor_claimed"),
        )

    def assess(
        self,
        material: VenueMaterial
        | ContentMaterial
        | HostMaterial
        | MusicMaterial
        | ExperienceMaterial,
    ) -> (
        VenueAssessment
        | ContentAssessment
        | HostAssessment
        | MusicAssessment
        | ExperienceAssessment
    ):
        """Satisfy every `*Model` protocol. The material selects the schema."""
        if isinstance(material, VenueMaterial):
            return self.assess_venue(material)
        if isinstance(material, ContentMaterial):
            return self.assess_content(material)
        if isinstance(material, HostMaterial):
            return self.assess_host(material)
        if isinstance(material, MusicMaterial):
            return self.assess_music(material)
        if isinstance(material, ExperienceMaterial):
            return self.assess_experience(material)
        raise TypeError(f"No assessment schema for {type(material).__name__}.")


def build_assessor(settings: Any) -> StructuredAssessor:
    """Chat client for `settings.agent_model_provider`. The client is lazy."""
    from app.config import PROVIDER_OLLAMA

    api_key = "ollama" if settings.agent_model_provider == PROVIDER_OLLAMA else settings.openai_api_key
    return StructuredAssessor(
        provider=settings.agent_model_provider,
        model=settings.agent_model,
        base_url=settings.agent_model_base_url,
        api_key=api_key,
    )
