"""SelectWinner — FR-EVAL-8, INV-6.

The organizer names one qualified bid. Award mail is returned for the web
process to send after the transaction commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.commands.override_score import bid_is_qualified
from app.domain.tender import (
    BidNotQualifiedError,
    NotTenderOwnerError,
    TenderAlreadyDecidedError,
    TenderNotClosableError,
    TenderNotFoundError,
    replay_tender,
)
from app.events.event_types import FINAL_RANKING_CALCULATED, WINNER_SELECTED
from app.notifications.award import AwardNotice, make_award_notice


@dataclass(frozen=True)
class SelectWinner:
    tender_id: str
    bid_id: str
    organizer_id: str


def select_winner(
    store, tenders, bids, users, command: SelectWinner, *, now: datetime | None = None
) -> tuple[AwardNotice, ...]:
    clock = _utc(now)
    state = replay_tender(store.read_stream(command.tender_id))
    if not state.tender_id:
        raise TenderNotFoundError("missing tender")
    if state.organizer_id != command.organizer_id:
        raise NotTenderOwnerError("only the organizing user can select the winner")
    if state.winner_bid_id:
        raise TenderAlreadyDecidedError("the tender is already decided")
    if not state.prices_revealed or not _ranked(store, command.tender_id):
        raise TenderNotClosableError("a winner needs a ranking")

    bid_events = store.read_stream(command.bid_id)
    if not bid_events or bid_events[0].event_data.get("tender_id") != command.tender_id:
        raise TenderNotFoundError("missing bid")
    if not bid_is_qualified(bid_events):
        raise BidNotQualifiedError("only a qualified bid can be selected")

    metadata = {"actor": command.organizer_id, "actor_role": "organizer"}
    store.append(
        command.tender_id,
        store.last_version(command.tender_id),
        WINNER_SELECTED,
        {
            "bid_id": command.bid_id,
            "selected_by": command.organizer_id,
            "selected_at": clock.isoformat(),
        },
        metadata,
    )
    tenders.apply_decided(command.tender_id, command.bid_id)
    return _notices(bids, users, command.tender_id, state.name, command.bid_id)


def _notices(bids, users, tender_id: str, tender_name: str, winner_id: str) -> tuple[AwardNotice, ...]:
    notices = []
    for row in bids.comparison_for(tender_id):
        if row.status == "withdrawn":
            continue
        user = users.get_by_id(row.supplier_id)
        if user is None or not user.email:
            continue
        notices.append(
            make_award_notice(
                to_email=user.email,
                tender_name=tender_name,
                won=row.bid_id == winner_id,
            )
        )
    return tuple(notices)


def _ranked(store, tender_id: str) -> bool:
    return any(
        event.event_type == FINAL_RANKING_CALCULATED for event in store.read_stream(tender_id)
    )


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)
