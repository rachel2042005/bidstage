"""Bid queries. Read models only — never the event stream (NFR-CQRS-2)."""

from __future__ import annotations

from app.projections.bids import AssessmentView, BidsProjection, SupplierBidView


def get_bid_for_assessment(bids: BidsProjection, bid_id: str) -> AssessmentView | None:
    return bids.assessment(bid_id)


def list_supplier_bids(bids: BidsProjection, supplier_id: str) -> list[SupplierBidView]:
    return bids.for_supplier(supplier_id)


def bid_count(bids: BidsProjection, tender_id: str) -> int:
    return bids.count_for(tender_id)


def active_bid(bids: BidsProjection, tender_id: str, supplier_id: str) -> SupplierBidView | None:
    for row in bids.for_supplier(supplier_id):
        if row.tender_id == tender_id and row.status == "submitted":
            return row
    return None
