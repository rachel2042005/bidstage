"""Embedding providers behind one protocol.

The only module that imports the OpenAI SDK for embeddings. Both providers go
through that SDK, because Ollama exposes an OpenAI-compatible `/v1/embeddings`
endpoint, so swapping between them is a base URL and a model name.

| Provider | Endpoint | Model | Dim | Key |
| --- | --- | --- | --- | --- |
| `openai` | `api.openai.com` | `text-embedding-3-small` | 1536 | required |
| `ollama` | `localhost:11434` | `qwen3-embedding:0.6b` | 1024 | none |

Both handle Hebrew, which is the requirement that rules the choice
(`NFR-VEC-4`). OpenAI is the default because it needs no local process and its
quality is known; Ollama costs nothing, needs no key, and keeps bid text on the
machine. The two are *not* interchangeable once documents are stored: vectors
from different models are incomparable, so switching means re-seeding every
collection. `vector_store.py` enforces that rather than trusting it.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from app import bootstrap

# Must precede the SDK import so the Windows trust store is in place before any
# client is constructed (`ENV-1`). Harmless for Ollama, which is plain HTTP to
# localhost and never touches the filtering proxy.
bootstrap.init()

from openai import OpenAI  # noqa: E402

from app.config import PROVIDER_OLLAMA, Settings  # noqa: E402

# Ollama requires the Authorization header to exist but ignores its contents.
_OLLAMA_PLACEHOLDER_KEY = "ollama"


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    provider: str
    model: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    """Serves both providers. They differ only in base URL, model and key."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        dim: int,
        base_url: str | None,
        api_key: str,
    ) -> None:
        self.provider = provider
        self.model = model
        self.dim = dim
        self._base_url = base_url
        self._api_key = api_key
        self._client: OpenAI | None = None

    def _ensure_client(self) -> OpenAI:
        # Built on first use, so constructing a store to inspect collection
        # metadata does not require a reachable provider.
        if self._client is None:
            if not self._api_key:
                raise EmbeddingError(
                    f"No API key for the {self.provider!r} embedding provider. "
                    f"Set OPENAI_API_KEY, or set EMBEDDING_PROVIDER=ollama to "
                    f"embed locally without a key."
                )
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._ensure_client().embeddings.create(
                model=self.model, input=list(texts)
            )
        except Exception as exc:
            raise EmbeddingError(
                f"{self.provider} embedding call failed for model "
                f"{self.model!r}: {exc}"
            ) from exc

        vectors = [item.embedding for item in response.data]
        # A width change makes every stored vector unqueryable, and Chroma
        # reports it only as a shape error deep inside a later call.
        for vector in vectors:
            if len(vector) != self.dim:
                raise EmbeddingError(
                    f"{self.model} returned {len(vector)} dimensions but the "
                    f"configured dimension is {self.dim}. Re-seed every "
                    f"collection before changing the model."
                )
        return vectors


def build_embedder(settings: Settings) -> Embedder:
    api_key = (
        _OLLAMA_PLACEHOLDER_KEY
        if settings.embedding_provider == PROVIDER_OLLAMA
        else settings.openai_api_key
    )
    return OpenAICompatibleEmbedder(
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        base_url=settings.embedding_base_url,
        api_key=api_key,
    )
