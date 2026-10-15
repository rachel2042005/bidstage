"""SearchOrganizerBids — rm_dashboard, rm_bid_ranking, and the bid search port.

FR-SRCH-4, FR-SRCH-6, T-AUTH-4. The port is asked only for tenders this
organizer may search. A hit has no price field.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.tender import (
    DECIDED,
    FAILED,
    UNDER_EVALUATION,
    NotTenderOwnerError,
    TenderNotFoundError,
)

_SEARCHABLE = frozenset({UNDER_EVALUATION, DECIDED, FAILED})
_TOP_K = 5


@dataclass(frozen=True)
class BidSearchHit:
    bid_id: str
    tender_id: str
    tender_name: str
    supplier_name: str
    excerpt: str


@dataclass(frozen=True)
class OrganizerBidSearch:
    hits: tuple[BidSearchHit, ...]
    unavailable: bool = False


def search_organizer_bids(
    tenders,
    bids,
    searcher,
    *,
    organizer_id: str,
    query: str,
    tender_id: str | None = None,
) -> OrganizerBidSearch:
    if tender_id is not None:
        _require_searchable(tenders, organizer_id, tender_id)
    text = query.strip()
    if not text:
        return OrganizerBidSearch(hits=())
    allowed = _allowed_tenders(tenders, organizer_id, tender_id)
    if not allowed:
        return OrganizerBidSearch(hits=())
    try:
        matches = searcher.search_bids(text, allowed, _TOP_K)
    except Exception:
        return OrganizerBidSearch(hits=(), unavailable=True)
    names = {entry.tender_id: entry.name for entry in tenders.dashboard_for(organizer_id)}
    rows = {}
    for allowed_id in allowed:
        for row in bids.comparison_for(allowed_id):
            rows[row.bid_id] = row
    allowed_set = set(allowed)
    hits: list[BidSearchHit] = []
    seen: set[str] = set()
    for match in matches:
        if match.tender_id not in allowed_set or match.bid_id in seen:
            continue
        row = rows.get(match.bid_id)
        if row is None or row.tender_id != match.tender_id:
            continue
        seen.add(match.bid_id)
        hits.append(
            BidSearchHit(
                bid_id=match.bid_id,
                tender_id=match.tender_id,
                tender_name=names[match.tender_id],
                supplier_name=row.supplier_name,
                excerpt=match.excerpt,
            )
        )
        if len(hits) == _TOP_K:
            break
    return OrganizerBidSearch(hits=tuple(hits))


def _require_searchable(tenders, organizer_id: str, tender_id: str) -> None:
    summary = tenders.get(tender_id)
    if summary is None:
        raise TenderNotFoundError(tender_id)
    if summary.organizer_id != organizer_id or summary.status not in _SEARCHABLE:
        raise NotTenderOwnerError(tender_id)


def _allowed_tenders(tenders, organizer_id: str, tender_id: str | None) -> list[str]:
    return [
        entry.tender_id
        for entry in tenders.dashboard_for(organizer_id)
        if entry.status in _SEARCHABLE and (tender_id is None or entry.tender_id == tender_id)
    ]
