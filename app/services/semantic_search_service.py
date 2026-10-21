"""Application boundary over VectorStore (`02-architecture.md` §6).

Query handlers and sub-agents call this module. It does not import chromadb
(`NFR-VEC-5`).
"""

from __future__ import annotations

from app.infrastructure.vector_store import (
    BID_CONCEPTS,
    REGULATIONS,
    RUBRICS,
    Match,
    VectorStore,
)

# Closed taxonomy (`00-overview.md` §3). An unrecognized type has no rubric.
REQUIREMENT_TYPES = frozenset(
    {"VENUE", "CONTENT", "HOST", "MUSIC", "EXPERIENCE"}
)


class SemanticSearchService:
    """Search and key lookup over the vector store."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    def search_bids(
        self,
        query: str,
        tender_id: str | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """Nearest bid concepts (`FR-SRCH-4`).

        `tender_id` is passed to the store as a metadata filter, so the
        restriction is applied before neighbours are chosen. Omitting it
        searches across tenders. Ownership of those tenders is the caller's
        job (`FR-SRCH-6`).
        """
        where = {"tender_id": tender_id} if tender_id is not None else None
        matches = self._store.query(BID_CONCEPTS, query, top_k, where=where)
        return [_hit(match) for match in matches]

    def get_rubric(self, requirement_type: str) -> dict | None:
        """The unchunked rubric for one requirement type (`NFR-VEC-3`, `T-VEC-3`).

        Returns `{id, text, metadata}`, or `None` when that type has not been
        seeded. A type outside the closed set raises `ValueError`.
        """
        normalized = requirement_type.upper()
        if normalized not in REQUIREMENT_TYPES:
            known = ", ".join(sorted(REQUIREMENT_TYPES))
            raise ValueError(
                f"Unknown requirement type {requirement_type!r}. "
                f"Known types: {known}."
            )
        matches = self._store.get(RUBRICS, [f"rubric:{normalized}"])
        if not matches:
            return None
        match = matches[0]
        return {
            "id": match.id,
            "text": match.document,
            "metadata": dict(match.metadata),
        }

    def search_regulations(self, query: str, top_k: int = 3) -> list[dict]:
        """Nearest regulations. `score` is cosine similarity, closest first."""
        matches = self._store.query(REGULATIONS, query, top_k)
        return [_hit(match) for match in matches]


def _hit(match: Match) -> dict:
    """Cosine similarity from the store's cosine distance (`02-architecture.md` §6)."""
    return {
        "id": match.id,
        "text": match.document,
        "metadata": dict(match.metadata),
        "score": 1.0 - match.distance,
    }
