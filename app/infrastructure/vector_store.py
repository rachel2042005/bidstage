"""Chroma adapter behind the VectorStore port (`02-architecture.md` §6).

The only module in the project that imports `chromadb` (`NFR-VEC-5`). Sub-agents
and query handlers depend on the `VectorStore` protocol, so a hosted store is
one new adapter plus a config value.

Two properties of this adapter are load-bearing:

**Server mode only.** Chroma is thread-safe but not process-safe, and both the
Flask process and the agent need the store (`ENV-4`). `chromadb.HttpClient` is
therefore the only client ever constructed here; `PersistentClient` is
forbidden. Start the server with `scripts/run_chroma.ps1`.

**Embeddings are computed in this process, never by the Chroma server.** The
server is launched by a bare `chroma run` that no code of ours wraps, so
`truststore.inject_into_ssl()` cannot be injected into it and any outbound
OpenAI call it made would fail against the filtering proxy (`ENV-1`). Owning
the client here keeps injection ahead of construction, and keeps the API key
out of the server entirely. Which provider does the embedding is `embeddings.py`'s
concern, selected by `EMBEDDING_PROVIDER`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from app import bootstrap

bootstrap.init()

import chromadb  # noqa: E402

from app.config import Settings, load_settings  # noqa: E402
from app.infrastructure.embeddings import Embedder, build_embedder  # noqa: E402

BID_CONCEPTS = "bid_concepts"
PORTFOLIOS = "portfolios"
RUBRICS = "rubrics"
REGULATIONS = "regulations"

# Fixed set. A typo'd collection name would otherwise be created silently and
# return nothing forever.
COLLECTIONS: tuple[str, ...] = (BID_CONCEPTS, PORTFOLIOS, RUBRICS, REGULATIONS)

# Chroma defaults to squared L2. Cosine is the conventional pairing with OpenAI
# embeddings, and the metric is fixed when a collection is created: changing it
# later means dropping and re-embedding everything.
DISTANCE_METRIC = "cosine"
_COLLECTION_METADATA: dict[str, Any] = {"hnsw:space": DISTANCE_METRIC}


class VectorStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class Match:
    id: str
    document: str
    metadata: dict
    distance: float


class VectorStore(Protocol):
    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        documents: Sequence[str],
        metadata: Sequence[dict],
    ) -> None: ...

    def query(
        self,
        collection: str,
        text: str,
        k: int,
        where: dict | None = None,
    ) -> list[Match]: ...

    def get(self, collection: str, ids: Sequence[str]) -> list[Match]: ...


class ChromaVectorStore:
    """Implements `VectorStore` against Chroma running in server mode."""

    def __init__(
        self,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._client = chromadb.HttpClient(
            host=self._settings.chroma_host,
            port=self._settings.chroma_port,
        )
        self._embedder = embedder or build_embedder(self._settings)

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embedder.embed(texts)

    # -- collections ------------------------------------------------------

    def _collection(self, name: str) -> Any:
        """Every collection access goes through here, so none can be created
        ad hoc with Chroma's default L2 metric."""
        if name not in COLLECTIONS:
            raise VectorStoreError(
                f"Unknown collection {name!r}. Known collections: "
                f"{', '.join(COLLECTIONS)}."
            )
        handle = self._client.get_or_create_collection(
            name=name,
            metadata={
                **_COLLECTION_METADATA,
                # Stamped so a later provider switch is caught here rather
                # than returning plausible-looking nonsense.
                "embedding_model": self._embedder.model,
                "embedding_dim": self._embedder.dim,
            },
            # Vectors are always supplied explicitly, so Chroma must not fall
            # back to its bundled ONNX MiniLM function, which is English-only
            # and would quietly degrade Hebrew retrieval (`NFR-VEC-4`).
            embedding_function=None,
        )
        self._assert_embeddings_match(handle)
        return handle

    def _assert_embeddings_match(self, handle: Any) -> None:
        """Vectors from two models are not comparable, and nothing downstream
        can detect it: the search simply returns the wrong documents."""
        stored = (handle.metadata or {}).get("embedding_model")
        if stored and stored != self._embedder.model:
            raise VectorStoreError(
                f"Collection {handle.name!r} was built with {stored!r} but the "
                f"configured provider {self._embedder.provider!r} uses "
                f"{self._embedder.model!r} ({self._embedder.dim} dims). "
                f"Vectors from different models cannot be compared. Run "
                f"reset_collections() and re-seed."
            )

    def ensure_collections(self) -> list[str]:
        """Idempotent. Safe to call at startup and from the seed script."""
        return [self._collection(name).name for name in COLLECTIONS]

    def reset_collections(self) -> list[str]:
        """Drop and recreate all four, discarding every vector.

        The destination for a provider switch. Safe because embeddings are a
        derived index rebuilt by the seed script (`ENV-6`), never a source of
        truth; the events in SQL Server are untouched.
        """
        for name in COLLECTIONS:
            try:
                self._client.delete_collection(name)
            except Exception:
                # Absent is the desired end state, so a missing collection is
                # not an error worth distinguishing here.
                pass
        return self.ensure_collections()

    def heartbeat(self) -> int:
        """Nanosecond timestamp from the server. Raises if it is unreachable."""
        return self._client.heartbeat()

    def distance_metric(self, collection: str) -> str | None:
        """The metric a collection was actually created with.

        Chroma reports it under `metadata` or under `configuration` depending
        on how the collection was created, so both are checked.
        """
        handle = self._collection(collection)
        metadata = handle.metadata or {}
        if metadata.get("hnsw:space"):
            return str(metadata["hnsw:space"])
        configuration = getattr(handle, "configuration", None) or {}
        hnsw = configuration.get("hnsw") or {}
        space = hnsw.get("space")
        return str(space) if space else None

    # -- documents --------------------------------------------------------

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        documents: Sequence[str],
        metadata: Sequence[dict],
    ) -> None:
        if not (len(ids) == len(documents) == len(metadata)):
            raise VectorStoreError(
                f"ids, documents and metadata must be the same length; got "
                f"{len(ids)}, {len(documents)}, {len(metadata)}."
            )
        if not ids:
            return
        self._collection(collection).upsert(
            ids=list(ids),
            documents=list(documents),
            metadatas=[dict(entry) for entry in metadata],
            embeddings=self.embed(documents),
        )

    def query(
        self,
        collection: str,
        text: str,
        k: int,
        where: dict | None = None,
    ) -> list[Match]:
        result = self._collection(collection).query(
            query_embeddings=self.embed([text]),
            n_results=k,
            # Chroma rejects an empty filter, so absent means absent.
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        return _to_matches(result)

    def get(self, collection: str, ids: Sequence[str]) -> list[Match]:
        """Load documents by id. Does not embed (`02-architecture.md` §6).

        Absent ids are omitted. `distance` is 0.0 because a key lookup is not
        a ranking; callers that need a score use `query`.
        """
        if not ids:
            return []
        result = self._collection(collection).get(
            ids=list(ids),
            include=["documents", "metadatas"],
        )
        return _to_get_matches(result, ids)

    def delete(self, collection: str, ids: Sequence[str]) -> None:
        """Vectors are a derived index, not business state, so deleting here
        breaks no event-sourcing rule: the seed script rebuilds them (`ENV-6`).
        """
        if not ids:
            return
        self._collection(collection).delete(ids=list(ids))


def _to_get_matches(result: Any, requested_ids: Sequence[str]) -> list[Match]:
    """Chroma's `get` returns flat lists, unlike `query`, and not necessarily
    in request order. Rebuild that order and drop ids the collection lacks."""
    ids = result.get("ids") or []
    documents = result.get("documents") or [""] * len(ids)
    metadatas = result.get("metadatas") or [None] * len(ids)
    found = {
        match_id: Match(
            id=match_id,
            document=document or "",
            metadata=dict(metadata or {}),
            distance=0.0,
        )
        for match_id, document, metadata in zip(ids, documents, metadatas)
    }
    return [found[match_id] for match_id in requested_ids if match_id in found]


def _to_matches(result: Any) -> list[Match]:
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0] or [""] * len(ids)
    metadatas = (result.get("metadatas") or [[]])[0] or [None] * len(ids)
    distances = (result.get("distances") or [[]])[0] or [0.0] * len(ids)
    return [
        Match(
            id=match_id,
            document=document or "",
            metadata=dict(metadata or {}),
            distance=float(distance),
        )
        for match_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances
        )
    ]
