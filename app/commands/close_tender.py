"""CloseTender — FR-EVAL-1, FR-EVAL-11.

An open tender closes when its deadline has passed, or when the organizer
closes it explicitly. bid_count is bids still submitted. Zero submitted bids
also append TenderFailed with reason no_bids_submitted. This command does
not start the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.tender import (
    OPEN,
    DeadlineStillOpenError,
    NotTenderOwnerError,
    TenderAlreadyClosedError,
    TenderNotClosableError,
    TenderNotFoundError,
    replay_tender,
)
from app.events.event_store import NewEvent
from app.events.event_types import TENDER_CLOSED_FOR_BIDS, TENDER_FAILED


@dataclass(frozen=True)
class CloseTender:
    tender_id: str
    organizer_id: str
    explicit: bool = False


def close_tender(store, tenders, bids, command: CloseTender, *, now: datetime | None = None) -> None:
    clock = _utc(now)
    state = replay_tender(store.read_stream(command.tender_id))
    if not state.tender_id:
        raise TenderNotFoundError("missing tender")
    if state.organizer_id != command.organizer_id:
        raise NotTenderOwnerError("only the organizing user can close this tender")
    if state.status != OPEN:
        if state.closed_at is not None or state.failed_at is not None:
            raise TenderAlreadyClosedError("tender is already closed")
        raise TenderNotClosableError("only an open tender can be closed")
    if not command.explicit and (state.deadline is None or clock < state.deadline):
        raise DeadlineStillOpenError("the deadline has not passed")

    count = bids.count_for(command.tender_id)
    metadata = {"actor": command.organizer_id, "actor_role": "organizer"}
    events = [
        NewEvent(
            TENDER_CLOSED_FOR_BIDS,
            {"closed_at": clock.isoformat(), "bid_count": count},
            metadata,
        )
    ]
    if count == 0:
        events.append(
            NewEvent(
                TENDER_FAILED,
                {"reason": "no_bids_submitted", "failed_at": clock.isoformat()},
                metadata,
            )
        )
    store.append_many(command.tender_id, store.last_version(command.tender_id), events)
    tenders.apply_closed(command.tender_id, events[0].event_data)
    if count == 0:
        tenders.apply_failed(command.tender_id, events[1].event_data)


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)
