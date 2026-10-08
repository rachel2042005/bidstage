"""Tender aggregate. Pure replay — no I/O (NFR-ES-3).

Status is derived from the stream (specs/03-domain-events.md §5). Publication
rules live here so a command cannot append TenderPublished unless the draft
already satisfies FR-ENT-3 and FR-ENT-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.user import DomainError, UnknownEventTypeError
from app.events.event_store import StoredEvent
from app.events.event_types import (
    REQUIREMENT_ADDED,
    TENDER_CREATED,
    TENDER_PUBLISHED,
)

CULTURE = "culture"
SPORTS = "sports"
EVENT_TYPES = frozenset({CULTURE, SPORTS})

VENUE = "VENUE"
CONTENT = "CONTENT"
HOST = "HOST"
MUSIC = "MUSIC"
EXPERIENCE = "EXPERIENCE"
REQUIREMENT_TYPES = frozenset({VENUE, CONTENT, HOST, MUSIC, EXPERIENCE})

DRAFT = "draft"
OPEN = "open"

CURRENCY = "ILS"


class PastDeadlineError(DomainError):
    pass


class WeightSumError(DomainError):
    def __init__(self, total: int) -> None:
        super().__init__(f"weights sum to {total}")
        self.total = total


class NoRequirementsError(DomainError):
    pass


class InvalidEventTypeError(DomainError):
    pass


class InvalidRequirementTypeError(DomainError):
    pass


class InvalidWeightError(DomainError):
    pass


class InvalidAlphaError(DomainError):
    pass


class InvalidBudgetError(DomainError):
    pass


class InvalidTenderError(DomainError):
    pass


class TenderNotFoundError(DomainError):
    pass


class TenderAlreadyPublishedError(DomainError):
    pass


class NotTenderOwnerError(DomainError):
    pass


class IllegalEventOrderError(DomainError):
    pass


@dataclass(frozen=True)
class RequirementState:
    requirement_id: str
    requirement_type: str
    weight: int
    is_threshold: bool
    description: str


@dataclass(frozen=True)
class TenderState:
    tender_id: str
    name: str
    event_type: str
    event_date: date | None
    region: str
    budget_amount: int
    budget_currency: str
    deadline: datetime | None
    alpha: Decimal
    organizer_id: str
    requirements: tuple[RequirementState, ...]
    published_at: datetime | None = None

    @property
    def weight_total(self) -> int:
        return sum(item.weight for item in self.requirements)

    @property
    def status(self) -> str:
        if not self.tender_id:
            return ""
        if self.published_at is not None:
            return OPEN
        return DRAFT


def empty_tender() -> TenderState:
    return TenderState(
        tender_id="",
        name="",
        event_type="",
        event_date=None,
        region="",
        budget_amount=0,
        budget_currency="",
        deadline=None,
        alpha=Decimal("0"),
        organizer_id="",
        requirements=(),
    )


def tender_stream_id(tender_id: str) -> str:
    """Tender streams are the tender UUID (specs/03-domain-events.md §1)."""
    return tender_id


def parse_alpha(value: Decimal | str | int) -> Decimal:
    try:
        alpha = Decimal(str(value))
    except Exception as exc:
        raise InvalidAlphaError("alpha must be between 0 and 1") from exc
    if alpha.is_nan() or alpha < 0 or alpha > 1 or alpha.as_tuple().exponent < -2:
        raise InvalidAlphaError("alpha must be between 0 and 1")
    return alpha


def parse_weight(value: int | str) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidWeightError("weight must be an integer from 1 to 100")
    if isinstance(value, str):
        if not value.strip().isdigit():
            raise InvalidWeightError("weight must be an integer from 1 to 100")
        value = int(value.strip())
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidWeightError("weight must be an integer from 1 to 100")
    if value <= 0 or value > 100:
        raise InvalidWeightError("weight must be an integer from 1 to 100")
    return value


def assert_can_publish(state: TenderState, now: datetime) -> None:
    if not state.tender_id:
        raise TenderNotFoundError("missing tender")
    if state.published_at is not None:
        raise TenderAlreadyPublishedError("tender is already published")
    if not state.requirements:
        raise NoRequirementsError("a tender cannot be published with zero requirements")
    total = state.weight_total
    if total != 100:
        raise WeightSumError(total)
    if state.deadline is None or state.deadline <= _as_utc(now):
        raise PastDeadlineError("deadline must still be in the future")


def apply_tender(state: TenderState, event: StoredEvent) -> TenderState:
    if event.event_type == TENDER_CREATED:
        if state.tender_id:
            raise IllegalEventOrderError("TenderCreated on an existing stream")
        if event.version != 1:
            raise IllegalEventOrderError("TenderCreated must be version 1")
        return _apply_created(event)
    if event.event_type == REQUIREMENT_ADDED:
        if not state.tender_id:
            raise IllegalEventOrderError("RequirementAdded before TenderCreated")
        if state.published_at is not None:
            raise IllegalEventOrderError("RequirementAdded after TenderPublished")
        return _apply_requirement(state, event)
    if event.event_type == TENDER_PUBLISHED:
        if not state.tender_id:
            raise IllegalEventOrderError("TenderPublished before TenderCreated")
        if state.published_at is not None:
            raise IllegalEventOrderError("TenderPublished twice")
        published_at = _parse_timestamp(event.event_data["published_at"])
        return TenderState(
            tender_id=state.tender_id,
            name=state.name,
            event_type=state.event_type,
            event_date=state.event_date,
            region=state.region,
            budget_amount=state.budget_amount,
            budget_currency=state.budget_currency,
            deadline=state.deadline,
            alpha=state.alpha,
            organizer_id=state.organizer_id,
            requirements=state.requirements,
            published_at=published_at,
        )
    raise UnknownEventTypeError(event.event_type)


def replay_tender(events: list[StoredEvent]) -> TenderState:
    state = empty_tender()
    previous = 0
    for event in events:
        if event.event_type == TENDER_CREATED and event.version != 1:
            raise IllegalEventOrderError("TenderCreated must be version 1")
        if event.version != previous + 1:
            raise IllegalEventOrderError(
                f"events must be contiguous; saw version {event.version} after {previous}"
            )
        if previous == 0 and event.event_type != TENDER_CREATED:
            raise IllegalEventOrderError("version 1 must be TenderCreated")
        state = apply_tender(state, event)
        previous = event.version
    return state


def _apply_created(event: StoredEvent) -> TenderState:
    data = event.event_data
    budget = data["estimated_budget"]
    return TenderState(
        tender_id=event.stream_id,
        name=data["name"],
        event_type=data["event_type"],
        event_date=date.fromisoformat(data["event_date"]),
        region=data["region"],
        budget_amount=int(budget["amount"]),
        budget_currency=str(budget["currency"]),
        deadline=_parse_timestamp(data["deadline"]),
        alpha=Decimal(data["alpha"]),
        organizer_id=data["organizer_id"],
        requirements=(),
    )


def _apply_requirement(state: TenderState, event: StoredEvent) -> TenderState:
    data = event.event_data
    requirement = RequirementState(
        requirement_id=data["requirement_id"],
        requirement_type=data["type"],
        weight=int(data["weight"]),
        is_threshold=bool(data["is_threshold"]),
        description=data["description"],
    )
    return TenderState(
        tender_id=state.tender_id,
        name=state.name,
        event_type=state.event_type,
        event_date=state.event_date,
        region=state.region,
        budget_amount=state.budget_amount,
        budget_currency=state.budget_currency,
        deadline=state.deadline,
        alpha=state.alpha,
        organizer_id=state.organizer_id,
        requirements=state.requirements + (requirement,),
        published_at=state.published_at,
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
