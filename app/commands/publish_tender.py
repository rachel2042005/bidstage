"""PublishTender — FR-ENT-1, FR-ENT-3, FR-ENT-4.

The draft step appends TenderCreated (and any requirements included with it).
Publication appends TenderPublished only when the weights sum to exactly 100,
at least one requirement exists, and the deadline is still in the future.

Both events are attributed to PublishTender (specs/03-domain-events.md §2).
There is no separate CreateTender command.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.tender import (
    CURRENCY,
    EVENT_TYPES,
    REQUIREMENT_TYPES,
    InvalidAlphaError,
    InvalidBudgetError,
    InvalidEventTypeError,
    InvalidRequirementTypeError,
    InvalidTenderError,
    InvalidWeightError,
    NoRequirementsError,
    NotTenderOwnerError,
    PastDeadlineError,
    TenderNotFoundError,
    WeightSumError,
    assert_can_publish,
    parse_alpha,
    parse_weight,
    replay_tender,
    tender_stream_id,
)
from app.events.event_store import NewEvent
from app.events.event_types import REQUIREMENT_ADDED, TENDER_CREATED, TENDER_PUBLISHED


@dataclass(frozen=True)
class RequirementSpec:
    requirement_type: str
    weight: int
    is_threshold: bool
    description: str


@dataclass(frozen=True)
class PublishTender:
    organizer_id: str
    name: str = ""
    event_type: str = ""
    event_date: date | None = None
    region: str = ""
    budget_amount: int = 0
    budget_currency: str = CURRENCY
    deadline: datetime | None = None
    alpha: Decimal | str = Decimal("0.6")
    requirements: tuple[RequirementSpec, ...] = ()
    tender_id: str | None = None
    publish: bool = False


def publish_tender(store, tenders, command: PublishTender, *, now: datetime | None = None) -> str:
    clock = _utc(now)
    if command.tender_id:
        return _publish_existing(store, tenders, command, clock)
    return _create(store, tenders, command, clock)


def _create(store, tenders, command: PublishTender, now: datetime) -> str:
    tender_id = str(uuid.uuid4())
    created = _created_payload(command, now)
    requirement_payloads = [
        _requirement_payload(spec) for spec in command.requirements
    ]
    events = [
        NewEvent(
            TENDER_CREATED,
            created,
            {"actor": command.organizer_id, "actor_role": "organizer"},
        )
    ]
    events.extend(
        NewEvent(
            REQUIREMENT_ADDED,
            payload,
            {"actor": command.organizer_id, "actor_role": "organizer"},
        )
        for payload in requirement_payloads
    )
    if command.publish:
        _assert_new_tender_can_publish(created, requirement_payloads, now)
        events.append(
            NewEvent(
                TENDER_PUBLISHED,
                {"published_at": now.isoformat()},
                {"actor": command.organizer_id, "actor_role": "organizer"},
            )
        )
    stream_id = tender_stream_id(tender_id)
    store.append_many(stream_id, 0, events)
    tenders.apply_created(stream_id, created)
    for payload in requirement_payloads:
        tenders.apply_requirement(stream_id, payload)
    if command.publish:
        tenders.apply_published(stream_id, {"published_at": now.isoformat()})
    return tender_id


def _publish_existing(store, tenders, command: PublishTender, now: datetime) -> str:
    if not command.publish:
        raise InvalidTenderError("an existing tender is published with publish=True")
    tender_id = command.tender_id or ""
    stream_id = tender_stream_id(tender_id)
    events = store.read_stream(stream_id)
    if not events:
        raise TenderNotFoundError(tender_id)
    state = replay_tender(events)
    if state.organizer_id != command.organizer_id:
        raise NotTenderOwnerError(tender_id)
    assert_can_publish(state, now)
    payload = {"published_at": now.isoformat()}
    expected = store.last_version(stream_id)
    store.append(
        stream_id,
        expected,
        TENDER_PUBLISHED,
        payload,
        metadata={"actor": command.organizer_id, "actor_role": "organizer"},
    )
    tenders.apply_published(stream_id, payload)
    return tender_id


def _created_payload(command: PublishTender, now: datetime) -> dict:
    organizer_id = command.organizer_id.strip()
    name = command.name.strip()
    region = " ".join(command.region.split())
    if not organizer_id:
        raise InvalidTenderError("organizer_id is required")
    if not name or len(name) > 200:
        raise InvalidTenderError("name is required")
    if command.event_type not in EVENT_TYPES:
        raise InvalidEventTypeError("event type must be culture or sports")
    if not isinstance(command.event_date, date) or isinstance(command.event_date, datetime):
        raise InvalidTenderError("event_date is required")
    if not region or len(region) > 100:
        raise InvalidTenderError("region is required")
    if command.budget_currency != CURRENCY:
        raise InvalidBudgetError("budget currency must be ILS")
    if isinstance(command.budget_amount, bool) or not isinstance(command.budget_amount, int):
        raise InvalidBudgetError("budget must be a positive number of agorot")
    if command.budget_amount <= 0:
        raise InvalidBudgetError("budget must be a positive number of agorot")
    if command.deadline is None:
        raise PastDeadlineError("deadline must be in the future")
    deadline = _utc(command.deadline)
    if deadline <= now:
        raise PastDeadlineError("deadline must be in the future")
    try:
        alpha = parse_alpha(command.alpha)
    except InvalidAlphaError:
        raise
    return {
        "name": name,
        "event_type": command.event_type,
        "event_date": command.event_date.isoformat(),
        "region": region,
        "estimated_budget": {"amount": command.budget_amount, "currency": CURRENCY},
        "deadline": deadline.isoformat(),
        "alpha": format(alpha, "f"),
        "organizer_id": organizer_id,
    }


def _requirement_payload(spec: RequirementSpec) -> dict:
    if spec.requirement_type not in REQUIREMENT_TYPES:
        raise InvalidRequirementTypeError(spec.requirement_type)
    weight = parse_weight(spec.weight)
    if not isinstance(spec.is_threshold, bool):
        raise InvalidTenderError("is_threshold must be true or false")
    description = spec.description.strip()
    if not description or len(description) > 4000:
        raise InvalidTenderError("description is required")
    return {
        "requirement_id": str(uuid.uuid4()),
        "type": spec.requirement_type,
        "weight": weight,
        "is_threshold": spec.is_threshold,
        "description": description,
    }


def _assert_new_tender_can_publish(created: dict, requirements: list[dict], now: datetime) -> None:
    if not requirements:
        raise NoRequirementsError("a tender cannot be published with zero requirements")
    total = sum(item["weight"] for item in requirements)
    if total != 100:
        raise WeightSumError(total)
    if datetime.fromisoformat(created["deadline"]) <= now:
        raise PastDeadlineError("deadline must still be in the future")


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)
