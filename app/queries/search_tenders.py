"""SearchTenders — rm_tender_search only (NFR-CQRS-2, FR-SRCH-1, FR-SRCH-2).

Free text matches the projected search document: tender name, region, and
requirement descriptions. The projection applies the filters in its own query
so a supplier listing does not load every tender first.
"""

from __future__ import annotations

from datetime import date

from app.projections.tenders import TenderSearch, TenderSummary, TendersProjection


def search_tenders(
    tenders: TendersProjection,
    *,
    organizer_id: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    region: str | None = None,
    event_date_from: date | None = None,
    event_date_to: date | None = None,
    keyword: str | None = None,
) -> list[TenderSummary]:
    return tenders.search(
        TenderSearch(
            organizer_id=organizer_id,
            status=status,
            event_type=event_type,
            region=region,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
            keyword=keyword,
        )
    )
