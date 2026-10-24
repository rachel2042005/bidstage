"""Organizer bid-search port (`FR-SRCH-4`, `FR-SRCH-6`).

`search_organizer_bids` calls `search_bids(query, tender_ids, top_k)` and
reads `bid_id`, `tender_id`, and `excerpt`. `SemanticSearchService` speaks
in store hits, so this is the object stored on `app.extensions["bid_search"]`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.semantic_search_service import SemanticSearchService


@dataclass(frozen=True)
class BidConceptHit:
    bid_id: str
    tender_id: str
    excerpt: str


class SemanticBidSearch:
    """Maps store hits onto the organizer search port."""

    def __init__(self, service: SemanticSearchService) -> None:
        self._service = service

    def search_bids(
        self,
        query: str,
        tender_ids: list[str],
        top_k: int = 5,
    ) -> list[BidConceptHit]:
        hits = self._service.search_bids_for_tenders(query, tender_ids, top_k)
        results: list[BidConceptHit] = []
        for hit in hits:
            metadata = hit["metadata"]
            bid_id = metadata.get("bid_id") or hit["id"]
            tender_id = metadata.get("tender_id")
            if not bid_id or not tender_id:
                continue
            results.append(
                BidConceptHit(
                    bid_id=str(bid_id),
                    tender_id=str(tender_id),
                    excerpt=hit["text"],
                )
            )
        return results
