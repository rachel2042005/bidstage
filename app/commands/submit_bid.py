"""SubmitBid — FR-ENT-5, FR-ENT-6, FR-ENT-8, FR-NOT-1, FR-NOT-4.

Appends BidSubmitted and PriceOfferSealed on a new bid stream, in that order,
then projects read models that do not contain the price. The receipt notifier
runs after that write. Its failure is swallowed so it cannot roll the bid back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.bid import (
    ActiveBidExistsError,
    IncompleteBidError,
    assert_tender_accepts_bids,
    normalize_responses,
    parse_sealed_price,
)
from app.domain.tender import CURRENCY, TenderNotFoundError, replay_tender
from app.events.event_store import NewEvent
from app.events.event_types import BID_SUBMITTED, PRICE_OFFER_SEALED
from app.notifications.receipt import make_receipt


@dataclass(frozen=True)
class BidResponse:
    requirement_id: str
    answer: str


@dataclass(frozen=True)
class SubmitBid:
    tender_id: str
    supplier_id: str
    concept: str
    responses: tuple[BidResponse, ...]
    price_amount: int
    supplier_email: str = ""
    supplier_display_name: str = ""
    currency: str = CURRENCY


def submit_bid(
    store,
    bids,
    command: SubmitBid,
    *,
    now: datetime | None = None,
    notifier=None,
    tenders=None,
) -> str:
    clock = _utc(now)
    tender = replay_tender(store.read_stream(command.tender_id))
    if not tender.tender_id:
        raise TenderNotFoundError("missing tender")
    assert_tender_accepts_bids(tender, clock)
    concept = command.concept.strip()
    if not concept:
        raise IncompleteBidError("a bid needs a concept")
    responses = normalize_responses(tender, command.responses)
    price_amount, currency = parse_sealed_price(command.price_amount, command.currency)
    if bids.has_active(command.tender_id, command.supplier_id):
        raise ActiveBidExistsError("this supplier already has an active bid")

    bid_id = str(uuid.uuid4())
    metadata = {"actor": command.supplier_id, "actor_role": "supplier"}
    submitted = {
        "tender_id": command.tender_id,
        "supplier_id": command.supplier_id,
        "concept": concept,
        "responses": [dict(item) for item in responses],
    }
    sealed = {
        "price_amount": price_amount,
        "currency": currency,
        "sealed_at": clock.isoformat(),
    }
    store.append_many(
        bid_id,
        0,
        [
            NewEvent(BID_SUBMITTED, submitted, metadata),
            NewEvent(PRICE_OFFER_SEALED, sealed, metadata),
        ],
    )
    bids.apply_submitted(
        bid_id,
        submitted,
        tender_name=tender.name,
        submitted_at=clock,
        supplier_name=command.supplier_display_name,
    )
    if tenders is not None:
        tenders.increment_bid_count(command.tender_id)
    if notifier is not None:
        receipt = make_receipt(
            to_email=command.supplier_email,
            tender_name=tender.name,
            bid_id=bid_id,
        )
        try:
            notifier.bid_received(receipt)
        except Exception:
            # FR-NOT-4: the bid is already appended. Email cannot undo it.
            pass
    return bid_id


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)
