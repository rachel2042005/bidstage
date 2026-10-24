"""Tender aggregate. Pure replay — no I/O (NFR-ES-3).

Status is derived from the stream (specs/03-domain-events.md §5). Publication
rules live here so a command cannot append TenderPublished unless the draft
already satisfies FR-ENT-3 and FR-ENT-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.user import DomainError, UnknownEventTypeError
from app.events.event_store import StoredEvent
from app.events.event_types import (
    REQUIREMENT_ADDED,
    TENDER_CLOSED_FOR_BIDS,
    TENDER_CREATED,
    TENDER_DETAILS_REVISED,
    TENDER_FAILED,
    TENDER_PUBLISHED,
    FINAL_RANKING_CALCULATED,
    PRICES_REVEALED,
    WINNER_SELECTED,
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
UNDER_EVALUATION = "under_evaluation"
DECIDED = "decided"
FAILED = "failed"
CANCELLED = "cancelled"

CURRENCY = "ILS"


class PastDeadlineError(DomainError):
    pass


class PastEventDateError(DomainError):
    pass


class DeadlineAfterEventError(DomainError):
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


class TenderNotClosableError(DomainError):
    pass


class TenderAlreadyClosedError(DomainError):
    pass


class DeadlineStillOpenError(DomainError):
    pass


class BidsNotAssessedError(DomainError):
    pass


class EmptyJustificationError(DomainError):
    pass


class ScoreOutOfRangeError(DomainError):
    pass


class RequirementNotScoredError(DomainError):
    pass


class BidNotQualifiedError(DomainError):
    pass


class TenderAlreadyDecidedError(DomainError):
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
    closed_at: datetime | None = None
    failed_at: datetime | None = None
    prices_revealed: bool = False
    winner_bid_id: str | None = None

    @property
    def weight_total(self) -> int:
        return sum(item.weight for item in self.requirements)

    @property
    def status(self) -> str:
        if not self.tender_id:
            return ""
        if self.failed_at is not None:
            return FAILED
        if self.winner_bid_id:
            return DECIDED
        if self.closed_at is not None:
            return UNDER_EVALUATION
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
    if not state.region.strip():
        raise InvalidTenderError("region is required")
    if state.budget_amount <= 0 or state.budget_currency != CURRENCY:
        raise InvalidBudgetError("budget must be a positive number of agorot")
    if state.event_date is None or state.event_date < israel_today(now):
        raise PastEventDateError("event date must be today or later")
    if any(not item.description.strip() for item in state.requirements):
        raise InvalidTenderError("description is required")
    if state.deadline >= event_day_end(state.event_date):
        raise DeadlineAfterEventError("deadline must be before the end of the event date")


def apply_tender(state: TenderState, event: StoredEvent) -> TenderState:
    if event.event_type == TENDER_CREATED:
        if state.tender_id:
            raise IllegalEventOrderError("TenderCreated on an existing stream")
        if event.version != 1:
            raise IllegalEventOrderError("TenderCreated must be version 1")
        return _apply_created(event)
    if event.event_type == TENDER_DETAILS_REVISED:
        if not state.tender_id:
            raise IllegalEventOrderError("TenderDetailsRevised before TenderCreated")
        if state.published_at is not None:
            raise IllegalEventOrderError("TenderDetailsRevised after TenderPublished")
        return _apply_revised(state, event)
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
    if event.event_type == TENDER_CLOSED_FOR_BIDS:
        if state.published_at is None:
            raise IllegalEventOrderError("TenderClosedForBids before TenderPublished")
        if state.closed_at is not None:
            raise IllegalEventOrderError("TenderClosedForBids twice")
        return _copy(state, closed_at=_parse_timestamp(event.event_data["closed_at"]))
    if event.event_type == TENDER_FAILED:
        if state.closed_at is None and state.published_at is None:
            raise IllegalEventOrderError("TenderFailed before the tender was published")
        return _copy(state, failed_at=_parse_timestamp(event.event_data["failed_at"]))
    if event.event_type == PRICES_REVEALED:
        if state.closed_at is None or state.failed_at is not None or state.prices_revealed:
            raise IllegalEventOrderError("PricesRevealed is out of order")
        return _copy(state, prices_revealed=True)
    if event.event_type == FINAL_RANKING_CALCULATED:
        if not state.prices_revealed:
            raise IllegalEventOrderError("FinalRankingCalculated before PricesRevealed")
        return state
    if event.event_type == WINNER_SELECTED:
        if not state.prices_revealed or state.winner_bid_id:
            raise IllegalEventOrderError("WinnerSelected is out of order")
        return _copy(state, winner_bid_id=str(event.event_data["bid_id"]))
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
    budget = data.get("estimated_budget")
    event_date = data.get("event_date")
    deadline = data.get("deadline")
    return TenderState(
        tender_id=event.stream_id,
        name=data["name"],
        event_type=data["event_type"],
        event_date=date.fromisoformat(event_date) if event_date else None,
        region=data.get("region") or "",
        budget_amount=int(budget["amount"]) if budget else 0,
        budget_currency=str(budget["currency"]) if budget else CURRENCY,
        deadline=_parse_timestamp(deadline) if deadline else None,
        alpha=Decimal(data["alpha"]),
        organizer_id=data["organizer_id"],
        requirements=(),
    )


def _apply_revised(state: TenderState, event: StoredEvent) -> TenderState:
    revised = _apply_created(event)
    return _copy(
        state,
        name=revised.name,
        event_type=revised.event_type,
        event_date=revised.event_date,
        region=revised.region,
        budget_amount=revised.budget_amount,
        budget_currency=revised.budget_currency,
        deadline=revised.deadline,
        alpha=revised.alpha,
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


def _copy(state: TenderState, **changes) -> TenderState:
    values = dict(
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
        published_at=state.published_at,
        closed_at=state.closed_at,
        failed_at=state.failed_at,
        prices_revealed=state.prices_revealed,
        winner_bid_id=state.winner_bid_id,
    )
    values.update(changes)
    return TenderState(**values)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _as_utc(parsed)


_ISRAEL = ZoneInfo("Asia/Jerusalem")


def israel_today(now: datetime) -> date:
    return _as_utc(now).astimezone(_ISRAEL).date()


def event_day_start(event_date: date) -> datetime:
    local = datetime(event_date.year, event_date.month, event_date.day, tzinfo=_ISRAEL)
    return local.astimezone(timezone.utc)


def event_day_end(event_date: date) -> datetime:
    """First instant of the next calendar day in Asia/Jerusalem."""
    return event_day_start(event_date + timedelta(days=1))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
