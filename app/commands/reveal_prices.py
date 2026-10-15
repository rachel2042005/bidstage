"""RevealPrices — FR-EVAL-5, FR-EVAL-6, INV-7.

The web process reads sealed prices from the bid stream only here. A bid that
is still short of a terminal assessment blocks the command, and nothing is
written. When every bid is disqualified, the tender fails and prices stay sealed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.scoring import QualifiedBid, rank_bids
from app.domain.tender import (
    UNDER_EVALUATION,
    BidsNotAssessedError,
    NotTenderOwnerError,
    TenderNotClosableError,
    TenderNotFoundError,
    replay_tender,
)
from app.events.event_store import NewEvent
from app.events.event_types import (
    BID_DISQUALIFIED,
    FINAL_RANKING_CALCULATED,
    PRICE_OFFER_SEALED,
    PRICES_REVEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    TENDER_FAILED,
)


@dataclass(frozen=True)
class RevealPrices:
    tender_id: str
    organizer_id: str


@dataclass(frozen=True)
class _Verdict:
    qualified: bool
    quality: Decimal | None
    price_amount: int
    submitted_at: datetime


def reveal_prices(store, tenders, bids, command: RevealPrices, *, now: datetime | None = None) -> None:
    clock = _utc(now)
    state = replay_tender(store.read_stream(command.tender_id))
    if not state.tender_id:
        raise TenderNotFoundError("missing tender")
    if state.organizer_id != command.organizer_id:
        raise NotTenderOwnerError("only the organizing user can reveal prices")
    if state.prices_revealed or state.failed_at is not None or state.status != UNDER_EVALUATION:
        raise TenderNotClosableError("prices can be revealed only during evaluation")

    rows = bids.comparison_for(command.tender_id)
    verdicts: list[tuple] = []
    for row in rows:
        verdict = _terminal(store.read_stream(row.bid_id), row.submitted_at)
        if verdict is None:
            raise BidsNotAssessedError("every bid must be assessed before prices are revealed")
        verdicts.append((row, verdict))

    qualified = [item for item in verdicts if item[1].qualified]
    prices = [
        {"bid_id": row.bid_id, "amount": verdict.price_amount} for row, verdict in verdicts
    ]
    metadata = {"actor": command.organizer_id, "actor_role": "organizer"}
    version = store.last_version(command.tender_id)
    if not qualified:
        failed = {"reason": "no qualified bids", "failed_at": clock.isoformat()}
        store.append(command.tender_id, version, TENDER_FAILED, failed, metadata)
        tenders.apply_failed(command.tender_id, failed)
        return

    ranked = rank_bids(
        [
            QualifiedBid(
                bid_id=row.bid_id,
                quality=verdict.quality if verdict.quality is not None else Decimal(0),
                price_amount=verdict.price_amount,
                submitted_at=verdict.submitted_at,
            )
            for row, verdict in verdicts
            if verdict.qualified and verdict.quality is not None
        ],
        state.alpha,
    )
    ranking = [
        {
            "bid_id": item.bid_id,
            "quality": format(item.quality, "f"),
            "price": item.price_amount,
            "price_score": format(item.price_score, "f"),
            "final_score": format(item.final_score, "f"),
            "rank": item.rank,
        }
        for item in ranked
    ]
    store.append_many(
        command.tender_id,
        version,
        [
            NewEvent(PRICES_REVEALED, {"prices": prices}, metadata),
            NewEvent(FINAL_RANKING_CALCULATED, {"ranking": ranking}, metadata),
        ],
    )
    bids.apply_revealed(ranking, prices)


def _terminal(events, submitted_at: datetime) -> _Verdict | None:
    quality: Decimal | None = None
    disqualified = False
    price: int | None = None
    for event in events:
        if event.event_type == PRICE_OFFER_SEALED:
            price = int(event.event_data["price_amount"])
        elif event.event_type == QUALITY_ASSESSMENT_COMPLETED:
            quality = Decimal(str(event.event_data["quality_score"]))
            disqualified = False
        elif event.event_type == BID_DISQUALIFIED:
            disqualified = True
            quality = None
    if price is None:
        return None
    if disqualified:
        return _Verdict(False, None, price, submitted_at)
    if quality is None:
        return None
    return _Verdict(True, quality, price, submitted_at)


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)
