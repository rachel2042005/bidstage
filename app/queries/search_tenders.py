"""SearchTenders — rm_tender_search only (NFR-CQRS-2, FR-SRCH-1, FR-SRCH-2)."""

from __future__ import annotations

from datetime import date

from app.projections.tenders import TenderSummary, TendersProjection


def search_tenders(
    tenders: TendersProjection,
    *,
    organizer_id: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    region: str | None = None,
    event_date_from: date | None = None,
    event_date_to: date | None = None,
) -> list[TenderSummary]:
    region_key = region.casefold().strip() if region else None
    matched = []
    for row in tenders.list_all():
        if organizer_id is not None and row.organizer_id != organizer_id:
            continue
        if status is not None and row.status != status:
            continue
        if event_type is not None and row.event_type != event_type:
            continue
        if region_key is not None and row.region.casefold() != region_key:
            continue
        if event_date_from is not None and row.event_date < event_date_from:
            continue
        if event_date_to is not None and row.event_date > event_date_to:
            continue
        matched.append(row)
    matched.sort(key=lambda row: (row.event_date, row.name))
    return matched
