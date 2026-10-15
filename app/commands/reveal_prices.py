"""Web-only reveal (`02-architecture.md` §3, `INV-3`).

`SqlRevealPricesHandler` replays the tender and its bid streams. A bid that
is not `QualityAssessmentCompleted` or `BidDisqualified` rejects the command
before any tender event is appended. When at least one bid is qualified, the handler loads sealed prices and
appends `PricesRevealed` then `FinalRankingCalculated`. When every bid is
terminal and none is qualified, it appends `TenderFailed` and leaves prices
sealed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pyodbc

from app.domain.scoring import Standing, rank_bids
from app.events.event_store import EventStore, NewEvent, StoredEvent
from app.events.event_types import (
    BID_DISQUALIFIED,
    BID_SUBMITTED,
    FINAL_RANKING_CALCULATED,
    PRICE_OFFER_SEALED,
    PRICES_REVEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    TENDER_CREATED,
    TENDER_FAILED,
)

_TERMINAL = frozenset({QUALITY_ASSESSMENT_COMPLETED, BID_DISQUALIFIED})


class RevealRejected(Exception):
    """At least one bid is still unassessed, so prices stay sealed."""


class SqlRevealPricesHandler:
    """Append tender outcome events. Never called from the agent."""

    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def handle(self, tender_id: str) -> None:
        store = EventStore(self._conn)
        streams = {
            bid_id: store.read_stream(bid_id) for bid_id in _bid_ids(store, tender_id)
        }
        for bid_id, events in streams.items():
            if not any(event.event_type in _TERMINAL for event in events):
                raise RevealRejected(f"{bid_id} is not terminal.")
        standings = [_standing(bid_id, events) for bid_id, events in streams.items()]
        qualified = [standing for standing in standings if standing is not None]
        if not qualified:
            store.append(
                tender_id,
                store.last_version(tender_id),
                TENDER_FAILED,
                {
                    "reason": "no_qualified_bids",
                    "failed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
            return
        alpha = _alpha(store.read_stream(tender_id))
        ranked = rank_bids(qualified, alpha)
        store.append_many(
            tender_id,
            store.last_version(tender_id),
            [
                NewEvent(PRICES_REVEALED, {"prices": _prices(streams)}),
                NewEvent(
                    FINAL_RANKING_CALCULATED,
                    {
                        "ranking": [
                            {
                                "bid_id": item.bid_id,
                                "quality": item.quality,
                                "price": item.price,
                                "price_score": item.price_score,
                                "final_score": item.final_score,
                                "rank": item.rank,
                            }
                            for item in ranked
                        ]
                    },
                ),
            ],
        )
        cursor = self._conn.cursor()
        for item in ranked:
            cursor.execute(
                _INSERT_RANKING,
                tender_id,
                item.bid_id,
                item.quality,
                item.price,
                item.price_score,
                item.final_score,
                item.rank,
            )
        cursor.close()


_INSERT_RANKING = """
INSERT INTO dbo.rm_bid_ranking (
    tender_id, bid_id, quality, price_amount, price_score, final_score, rank
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def _standing(bid_id: str, events: list[StoredEvent]) -> Standing | None:
    completed = _first(events, QUALITY_ASSESSMENT_COMPLETED)
    if completed is None:
        return None
    submitted = _first(events, BID_SUBMITTED)
    sealed = _first(events, PRICE_OFFER_SEALED)
    if submitted is None or sealed is None:
        raise ValueError(f"{bid_id} is qualified and has no sealed price.")
    return Standing(
        bid_id=bid_id,
        quality=float(completed.event_data["quality_score"]),
        price=int(sealed.event_data["price_amount"]),
        qualified=True,
        submitted_at=submitted.created_at,
    )


def _prices(streams: dict[str, list[StoredEvent]]) -> list[dict]:
    prices = []
    for bid_id, events in streams.items():
        sealed = _first(events, PRICE_OFFER_SEALED)
        if sealed is None:
            continue
        prices.append({"bid_id": bid_id, "amount": int(sealed.event_data["price_amount"])})
    return prices


def _alpha(events: list[StoredEvent]) -> float:
    created = _first(events, TENDER_CREATED)
    if created is None:
        raise ValueError("TenderCreated is missing, so alpha is unknown.")
    return float(created.event_data["alpha"])


def _first(events: list[StoredEvent], event_type: str) -> StoredEvent | None:
    for event in events:
        if event.event_type == event_type:
            return event
    return None


def _bid_ids(store: EventStore, tender_id: str) -> tuple[str, ...]:
    found: list[str] = []
    for event in store.read_all():
        if event.event_type != BID_SUBMITTED:
            continue
        if event.event_data.get("tender_id") != tender_id:
            continue
        if event.stream_id not in found:
            found.append(event.stream_id)
    return tuple(found)
