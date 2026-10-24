"""RevealPrices — FR-EVAL-5, FR-EVAL-6, INV-3, INV-7.

The web process reads sealed prices from the bid stream only here. A bid that
is still short of a terminal assessment blocks the command, and nothing is
written. When every bid is disqualified, the tender fails and prices stay sealed.

`SqlRevealPricesHandler` is the same rule for the assessment tests: it replays
the tender and its bid streams, and it never runs inside the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pyodbc

from app.domain.scoring import QualifiedBid, Standing, rank_bids
from app.domain.tender import (
    UNDER_EVALUATION,
    BidsNotAssessedError,
    NotTenderOwnerError,
    TenderNotClosableError,
    TenderNotFoundError,
    replay_tender,
)
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
    scores: tuple = ()


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

    for row, verdict in verdicts:
        if verdict.scores:
            bids.apply_scores(row.bid_id, [_revealed_score(item) for item in verdict.scores])

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
    scores: tuple = ()
    for event in events:
        if event.event_type == PRICE_OFFER_SEALED:
            price = int(event.event_data["price_amount"])
        elif event.event_type == QUALITY_ASSESSMENT_COMPLETED:
            quality = Decimal(str(event.event_data["quality_score"]))
            disqualified = False
            scores = tuple(event.event_data.get("requirement_scores") or ())
        elif event.event_type == BID_DISQUALIFIED:
            disqualified = True
            quality = None
    if price is None:
        return None
    if disqualified:
        return _Verdict(False, None, price, submitted_at, scores)
    if quality is None:
        return None
    return _Verdict(True, quality, price, submitted_at, scores)


def _revealed_score(item: dict) -> dict:
    return {
        "requirement_id": item["requirement_id"],
        "weight": int(item["weight"]),
        "is_threshold": bool(item["is_threshold"]),
        "agent_score": int(item["score"]),
        "score": int(item["score"]),
        "override_score": None,
        "override_justification": None,
    }


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


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
