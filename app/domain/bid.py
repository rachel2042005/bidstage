"""Bid aggregate checks. Pure — no I/O (NFR-ES-3).

A bid may be submitted only while its tender is open and the deadline has not
passed, with one non-empty answer per requirement and no second active bid
(FR-ENT-5, FR-ENT-8). The price is not part of this check's result: it is
sealed in a separate event.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.tender import CURRENCY, OPEN, TenderState
from app.domain.user import DomainError

SUBMITTED = "submitted"


class IncompleteBidError(DomainError):
    pass


class DeadlinePassedError(DomainError):
    pass


class ActiveBidExistsError(DomainError):
    pass


class TenderNotOpenError(DomainError):
    pass


class InvalidPriceError(DomainError):
    pass


def assert_tender_accepts_bids(tender: TenderState, now: datetime) -> None:
    if not tender.tender_id:
        raise TenderNotOpenError("tender is not open for bids")
    if tender.status != OPEN:
        raise TenderNotOpenError("tender is not open for bids")
    if tender.deadline is None or _as_utc(now) >= tender.deadline:
        raise DeadlinePassedError("the submission deadline has passed")


def normalize_responses(tender: TenderState, responses: tuple) -> tuple[dict, ...]:
    """One non-empty answer for every requirement, and nothing else."""
    expected = [item.requirement_id for item in tender.requirements]
    seen: dict[str, str] = {}
    for response in responses:
        requirement_id = response.requirement_id
        if requirement_id in seen:
            raise IncompleteBidError("a bid needs one response per requirement")
        seen[requirement_id] = response.answer.strip()
    if list(seen) != expected and set(seen) != set(expected):
        raise IncompleteBidError("a bid needs one response per requirement")
    if len(seen) != len(expected):
        raise IncompleteBidError("a bid needs one response per requirement")
    ordered = tuple(
        {"requirement_id": requirement_id, "answer": seen[requirement_id]}
        for requirement_id in expected
    )
    if any(not item["answer"] for item in ordered):
        raise IncompleteBidError("a bid needs one response per requirement")
    return ordered


def parse_sealed_price(amount: int, currency: str) -> tuple[int, str]:
    if type(amount) is not int or amount <= 0:
        raise InvalidPriceError("price must be a positive number of agorot")
    if currency != CURRENCY:
        raise InvalidPriceError("price currency must be ILS")
    return amount, currency


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
