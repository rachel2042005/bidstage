"""Settings loaded from .env. See specs/02-architecture.md §9."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app import bootstrap

PLACEHOLDER_MARKER = "YOUR_SERVER"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    db_connection_string: str
    openai_api_key: str
    tavily_api_key: str
    chroma_host: str
    chroma_port: int
    embedding_model: str
    embedding_dim: int
    flask_secret_key: str
    agent_poll_seconds: int
    quality_floor: int

    @property
    def db_is_configured(self) -> bool:
        return PLACEHOLDER_MARKER not in self.db_connection_string


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def load_settings() -> Settings:
    bootstrap.init()
    return Settings(
        db_connection_string=_required("SOMEE_DB_CONNECTION_STRING"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=int(os.getenv("EMBEDDING_DIM", "1536")),
        flask_secret_key=os.getenv("FLASK_SECRET_KEY", ""),
        agent_poll_seconds=int(os.getenv("AGENT_POLL_SECONDS", "60")),
        quality_floor=int(os.getenv("QUALITY_FLOOR", "60")),
    )
