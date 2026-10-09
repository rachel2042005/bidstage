"""Settings loaded from .env. See specs/02-architecture.md §9."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app import bootstrap

PLACEHOLDER_MARKER = "YOUR_SERVER"

PROVIDER_OPENAI = "openai"
PROVIDER_OLLAMA = "ollama"

# Model and dimension are properties of the provider, not free choices: a
# mismatch produces vectors that are silently incomparable. They are therefore
# derived from the provider rather than set alongside it.
EMBEDDING_PROVIDERS: dict[str, dict[str, object]] = {
    PROVIDER_OPENAI: {
        "model": "text-embedding-3-small",
        "dim": 1536,
        # None means the SDK's own default, api.openai.com.
        "base_url": None,
    },
    PROVIDER_OLLAMA: {
        "model": "qwen3-embedding:0.6b",
        "dim": 1024,
        "base_url": "http://localhost:11434/v1",
    },
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    db_connection_string: str
    openai_api_key: str
    tavily_api_key: str
    chroma_host: str
    chroma_port: int
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    embedding_base_url: str | None
    flask_secret_key: str
    agent_poll_seconds: int
    quality_floor: int

    @property
    def db_is_configured(self) -> bool:
        return PLACEHOLDER_MARKER not in self.db_connection_string

    @property
    def openai_is_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def embeddings_are_configured(self) -> bool:
        """Ollama runs on localhost and needs no key; OpenAI does."""
        if self.embedding_provider == PROVIDER_OLLAMA:
            return True
        return self.openai_is_configured

    @property
    def embedding_endpoint(self) -> str:
        return self.embedding_base_url or "https://api.openai.com/v1"

    @property
    def chroma_endpoint(self) -> str:
        """For diagnostics only. Clients take host and port separately."""
        return f"http://{self.chroma_host}:{self.chroma_port}"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _embedding_provider() -> str:
    provider = os.getenv("EMBEDDING_PROVIDER", PROVIDER_OPENAI).strip().lower()
    if provider not in EMBEDDING_PROVIDERS:
        raise ConfigError(
            f"EMBEDDING_PROVIDER={provider!r} is not supported. Choose one of: "
            f"{', '.join(sorted(EMBEDDING_PROVIDERS))}."
        )
    return provider


def load_settings() -> Settings:
    bootstrap.init()
    provider = _embedding_provider()
    defaults = EMBEDDING_PROVIDERS[provider]

    # EMBEDDING_MODEL and EMBEDDING_DIM remain available as overrides for an
    # unlisted model, but they are not needed to switch provider. Leaving them
    # unset is the safe path.
    model = os.getenv("EMBEDDING_MODEL", "").strip() or str(defaults["model"])
    dim = os.getenv("EMBEDDING_DIM", "").strip() or str(defaults["dim"])

    # Switching EMBEDDING_PROVIDER while a stale EMBEDDING_MODEL from the other
    # provider is still set would send one provider's model name to the other.
    for other, spec in EMBEDDING_PROVIDERS.items():
        if other != provider and model == spec["model"]:
            raise ConfigError(
                f"EMBEDDING_PROVIDER is {provider!r} but EMBEDDING_MODEL is set "
                f"to {model!r}, which belongs to the {other!r} provider. Remove "
                f"EMBEDDING_MODEL and EMBEDDING_DIM from .env to use the "
                f"{provider!r} defaults."
            )

    base_url = defaults["base_url"]
    if provider == PROVIDER_OLLAMA:
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or base_url

    return Settings(
        db_connection_string=_required("SOMEE_DB_CONNECTION_STRING"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        embedding_provider=provider,
        embedding_model=model,
        embedding_dim=int(dim),
        embedding_base_url=base_url if base_url else None,
        flask_secret_key=os.getenv("FLASK_SECRET_KEY", ""),
        agent_poll_seconds=int(os.getenv("AGENT_POLL_SECONDS", "60")),
        quality_floor=int(os.getenv("QUALITY_FLOOR", "60")),
    )
