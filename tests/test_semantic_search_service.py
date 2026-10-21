"""Semantic search application service.

Seam: the public methods of SemanticSearchService, against an in-memory
VectorStore. Live Chroma stays in smoke_test_vector_store.py.

T-VEC-3 — get_rubric returns one whole document.
Regulation ranking — cosine similarity is a hand-computed literal: identical
unit vectors score 1.0, orthogonal unit vectors score 0.0.
FR-SRCH-4 — search_bids applies tender_id before neighbours are chosen.
"""

from __future__ import annotations

from app.infrastructure.vector_store import BID_CONCEPTS, REGULATIONS, RUBRICS, Match
from app.services.semantic_search_service import SemanticSearchService

# Two bands, so a truncated rubric cannot satisfy the equality.
_VENUE_RUBRIC = (
    "# מחוון: מקום האירוע (VENUE)\n"
    "### 9–10\n"
    "אימות מלא של הקיבולת ושל עמידת התוכנית בתקנות.\n"
    "### 0\n"
    "המקום המוצהר אינו קיים.\n"
)


def _metadata_matches(metadata: dict, where: dict | None) -> bool:
    if not where:
        return True
    return all(metadata.get(key) == value for key, value in where.items())


def _cosine_distance(
    left: tuple[float, float], right: tuple[float, float]
) -> float:
    """Chroma cosine distance: 1 − cosine similarity."""
    dot = left[0] * right[0] + left[1] * right[1]
    left_norm = (left[0] ** 2 + left[1] ** 2) ** 0.5
    right_norm = (right[0] ** 2 + right[1] ** 2) ** 0.5
    return 1.0 - dot / (left_norm * right_norm)


class InMemoryVectorStore:
    """VectorStore double. Vectors are assigned by exact text, in 2-D."""

    def __init__(
        self, vectors: dict[str, tuple[float, float]] | None = None
    ) -> None:
        self._vectors = vectors or {}
        self._docs: dict[str, dict[str, tuple[str, dict]]] = {}

    def upsert(
        self,
        collection: str,
        ids: list[str],
        documents: list[str],
        metadata: list[dict],
    ) -> None:
        bucket = self._docs.setdefault(collection, {})
        for doc_id, document, meta in zip(ids, documents, metadata):
            bucket[doc_id] = (document, dict(meta))

    def get(self, collection: str, ids: list[str]) -> list[Match]:
        bucket = self._docs.get(collection, {})
        return [
            Match(
                id=doc_id,
                document=bucket[doc_id][0],
                metadata=dict(bucket[doc_id][1]),
                distance=0.0,
            )
            for doc_id in ids
            if doc_id in bucket
        ]

    def query(
        self,
        collection: str,
        text: str,
        k: int,
        where: dict | None = None,
    ) -> list[Match]:
        query_vector = self._vectors[text]
        bucket = self._docs.get(collection, {})
        matches = [
            Match(
                id=doc_id,
                document=document,
                metadata=dict(meta),
                distance=_cosine_distance(query_vector, self._vectors[document]),
            )
            for doc_id, (document, meta) in bucket.items()
            if _metadata_matches(meta, where)
        ]
        matches.sort(key=lambda match: match.distance)
        return matches[:k]


# Identical to the query vector (1, 0) → cosine similarity 1.
# Orthogonal to it (0, 1) → cosine similarity 0.
_SAFETY_QUERY = "בטיחות קהל באירוע המוני"
_CROWD_RULE = "יציאות חירום ברוחב 1.1 מטר לכל 600 איש."
_KITCHEN_RULE = "תפריט כשר, 500 מנות, צוות מלצרים."


def test_get_rubric_returns_the_whole_document_for_the_requirement_type() -> None:
    """T-VEC-3: rubric:VENUE comes back whole, including every band."""
    store = InMemoryVectorStore()
    store.upsert(
        RUBRICS,
        ids=["rubric:VENUE"],
        documents=[_VENUE_RUBRIC],
        metadata=[{"requirement_type": "VENUE", "rubric_version": "1"}],
    )
    service = SemanticSearchService(store)

    rubric = service.get_rubric("venue")

    assert rubric == {
        "id": "rubric:VENUE",
        "text": _VENUE_RUBRIC,
        "metadata": {"requirement_type": "VENUE", "rubric_version": "1"},
    }


def test_search_regulations_ranks_the_matching_rule_ahead_of_an_unrelated_one() -> None:
    """Identical unit vectors score 1.0; orthogonal unit vectors score 0.0."""
    store = InMemoryVectorStore(
        vectors={
            _SAFETY_QUERY: (1.0, 0.0),
            _CROWD_RULE: (1.0, 0.0),
            _KITCHEN_RULE: (0.0, 1.0),
        }
    )
    store.upsert(
        REGULATIONS,
        ids=["regulation:crowd", "regulation:kitchen"],
        documents=[_CROWD_RULE, _KITCHEN_RULE],
        metadata=[
            {"source": "mass_events_safety.md", "section": "משטרה וקהל"},
            {"source": "mass_events_safety.md", "section": "הסעדה"},
        ],
    )
    service = SemanticSearchService(store)

    hits = service.search_regulations(_SAFETY_QUERY)

    assert [hit["id"] for hit in hits] == ["regulation:crowd", "regulation:kitchen"]
    assert hits[0]["score"] == 1.0
    assert hits[1]["score"] == 0.0
    assert hits[0]["text"] == _CROWD_RULE
    assert hits[0]["metadata"]["section"] == "משטרה וקהל"


# The other tender's bid is identical to the query, so an unfiltered top-1
# returns it. The caller's bid is orthogonal and would lose that race.
_TRIBUTE_QUERY = "קטע מחווה"
_OTHER_TENDER_BID = "קטע מחווה ליוצרים שהלכו לעולמם."
_THIS_TENDER_BID = "במה מרכזית ומערכת תאורה לאולם."


def test_search_bids_keeps_only_the_requested_tender() -> None:
    """FR-SRCH-4: tender_id is applied before top_k neighbours are chosen."""
    store = InMemoryVectorStore(
        vectors={
            _TRIBUTE_QUERY: (1.0, 0.0),
            _OTHER_TENDER_BID: (1.0, 0.0),
            _THIS_TENDER_BID: (0.0, 1.0),
        }
    )
    store.upsert(
        BID_CONCEPTS,
        ids=["bid-other", "bid-mine"],
        documents=[_OTHER_TENDER_BID, _THIS_TENDER_BID],
        metadata=[
            {"tender_id": "t-2"},
            {"tender_id": "t-1"},
        ],
    )
    service = SemanticSearchService(store)

    hits = service.search_bids(_TRIBUTE_QUERY, tender_id="t-1", top_k=1)

    assert [hit["id"] for hit in hits] == ["bid-mine"]
    assert hits[0]["metadata"]["tender_id"] == "t-1"
    assert hits[0]["text"] == _THIS_TENDER_BID
