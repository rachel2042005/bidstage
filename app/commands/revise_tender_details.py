"""ReviseTenderDetails — FR-ENT-9.

Appends TenderDetailsRevised on a draft the organizer owns. A published
tender rejects the revision. Requirements are not part of this event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.commands.publish_tender import PublishTender, _created_payload, _utc
from app.domain.tender import (
    CURRENCY,
    NotTenderOwnerError,
    TenderAlreadyPublishedError,
    TenderNotFoundError,
    replay_tender,
    tender_stream_id,
)
from app.events.event_types import TENDER_DETAILS_REVISED


@dataclass(frozen=True)
class ReviseTenderDetails:
    tender_id: str
    organizer_id: str
    name: str = ""
    event_type: str = ""
    event_date: date | None = None
    region: str = ""
    budget_amount: int | None = None
    budget_currency: str = CURRENCY
    deadline: datetime | None = None
    alpha: Decimal | str = Decimal("0.6")


def revise_tender_details(store, tenders, command: ReviseTenderDetails, *, now: datetime | None = None) -> str:
    clock = _utc(now)
    tender_id = command.tender_id
    stream_id = tender_stream_id(tender_id)
    events = store.read_stream(stream_id)
    if not events:
        raise TenderNotFoundError(tender_id)
    state = replay_tender(events)
    organizer_id = command.organizer_id.strip()
    if state.organizer_id != organizer_id:
        raise NotTenderOwnerError(tender_id)
    if state.published_at is not None:
        raise TenderAlreadyPublishedError("only a draft can be revised")
    payload = _created_payload(
        PublishTender(
            organizer_id=state.organizer_id,
            name=command.name,
            event_type=command.event_type,
            event_date=command.event_date,
            region=command.region,
            budget_amount=command.budget_amount,
            budget_currency=command.budget_currency,
            deadline=command.deadline,
            alpha=command.alpha,
        ),
        clock,
    )
    expected = store.last_version(stream_id)
    store.append(
        stream_id,
        expected,
        TENDER_DETAILS_REVISED,
        payload,
        metadata={"actor": organizer_id, "actor_role": "organizer"},
    )
    tenders.apply_revised(stream_id, payload)
    return tender_id
