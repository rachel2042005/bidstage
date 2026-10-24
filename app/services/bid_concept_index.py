"""Derived index of bid concepts (`00-overview.md` §7).

`rm_bid_for_assessment` has the concept and no price column. Only tenders an
organizer can search are embedded. A second call skips ids already stored.
"""

from __future__ import annotations

from app.domain.tender import DECIDED, FAILED, UNDER_EVALUATION
from app.infrastructure.vector_store import BID_CONCEPTS, VectorStore

_SEARCHABLE = (UNDER_EVALUATION, DECIDED, FAILED)

_SELECT_CONCEPTS = """
SELECT a.bid_id, a.tender_id, a.concept
FROM dbo.rm_bid_for_assessment AS a
INNER JOIN dbo.rm_tender_detail AS t ON t.tender_id = a.tender_id
WHERE t.status IN (?, ?, ?)
"""


def index_bid_concepts(
    store: VectorStore,
    rows: list[tuple[str, str, str]],
) -> int:
    """Upsert concepts that are not already in `bid_concepts`. Returns how many."""
    pending: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for bid_id, tender_id, concept in rows:
        text = concept.strip()
        if not bid_id or not tender_id or not text or bid_id in seen:
            continue
        seen.add(bid_id)
        pending.append((bid_id, tender_id, text))
    if not pending:
        return 0
    present = {
        match.id
        for match in store.get(BID_CONCEPTS, [bid_id for bid_id, _, _ in pending])
    }
    missing = [row for row in pending if row[0] not in present]
    if not missing:
        return 0
    store.upsert(
        BID_CONCEPTS,
        [bid_id for bid_id, _, _ in missing],
        [concept for _, _, concept in missing],
        [
            {"bid_id": bid_id, "tender_id": tender_id}
            for bid_id, tender_id, _ in missing
        ],
    )
    return len(missing)


def index_stored_bid_concepts(store: VectorStore) -> int:
    """Index concepts for under-evaluation, decided, and failed tenders."""
    return index_bid_concepts(store, _load_searchable_concepts())


def _load_searchable_concepts() -> list[tuple[str, str, str]]:
    from app.events.db import transaction

    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(_SELECT_CONCEPTS, *_SEARCHABLE)
        rows = [(str(row[0]), str(row[1]), str(row[2] or "")) for row in cursor.fetchall()]
        cursor.close()
        return rows
