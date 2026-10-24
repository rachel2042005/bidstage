"""OverrideScore — FR-EVAL-7, INV-8.

ScoreOverridden stays on the bid. The new gate result follows it there, and
FinalRankingCalculated on the tender recomputes every qualified price score.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.scoring import QualifiedBid, RequirementMark, assess, rank_bids
from app.domain.tender import (
    BidsNotAssessedError,
    EmptyJustificationError,
    NotTenderOwnerError,
    RequirementNotScoredError,
    ScoreOutOfRangeError,
    TenderAlreadyDecidedError,
    TenderNotClosableError,
    TenderNotFoundError,
    replay_tender,
)
from app.events.event_store import NewEvent
from app.events.event_types import (
    BID_DISQUALIFIED,
    FINAL_RANKING_CALCULATED,
    PRICE_OFFER_SEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    REQUIREMENT_SCORED,
    SCORE_OVERRIDDEN,
)


@dataclass(frozen=True)
class OverrideScore:
    tender_id: str
    bid_id: str
    requirement_id: str
    new_score: int
    justification: str
    organizer_id: str


@dataclass(frozen=True)
class _Line:
    requirement_id: str
    weight: int
    is_threshold: bool
    agent_score: int
    effective: int
    overridden: bool = False
    note: str | None = None


def override_score(store, tenders, bids, command: OverrideScore, *, now: datetime | None = None) -> None:
    del tenders, now
    state = replay_tender(store.read_stream(command.tender_id))
    if not state.tender_id:
        raise TenderNotFoundError("missing tender")
    if state.organizer_id != command.organizer_id:
        raise NotTenderOwnerError("only the organizing user can override a score")
    if state.winner_bid_id:
        raise TenderAlreadyDecidedError("the tender is already decided")
    if not state.prices_revealed:
        raise TenderNotClosableError("scores can be overridden only after prices are revealed")

    justification = command.justification.strip()
    if not justification:
        raise EmptyJustificationError("justification is required")
    if type(command.new_score) is not int or command.new_score < 0 or command.new_score > 10:
        raise ScoreOutOfRangeError("score must be an integer from 0 to 10")

    bid_events = store.read_stream(command.bid_id)
    if not bid_events or bid_events[0].event_data.get("tender_id") != command.tender_id:
        raise TenderNotFoundError("missing bid")
    if not _assessed(bid_events):
        raise BidsNotAssessedError("the bid has not been assessed")
    lines = requirement_lines(state.requirements, bid_events)
    target = next((line for line in lines if line.requirement_id == command.requirement_id), None)
    if target is None:
        raise RequirementNotScoredError("requirement was not scored")

    updated = [
        replace(
            line,
            effective=command.new_score,
            overridden=True,
            note=justification,
        )
        if line.requirement_id == command.requirement_id
        else line
        for line in lines
    ]
    metadata = {"actor": command.organizer_id, "actor_role": "organizer"}
    store.append_many(
        command.bid_id,
        store.last_version(command.bid_id),
        [
            NewEvent(
                SCORE_OVERRIDDEN,
                {
                    "requirement_id": command.requirement_id,
                    "old_score": target.effective,
                    "new_score": command.new_score,
                    "justification": justification,
                    "overridden_by": command.organizer_id,
                },
                metadata,
            ),
            _gate_event(updated, metadata),
        ],
    )
    bids.apply_scores(command.bid_id, [score_row(line) for line in updated])
    _rerank(store, bids, state, command.organizer_id)


def requirement_lines(requirements, events) -> list[_Line]:
    agent: dict[str, int] = {}
    effective: dict[str, int] = {}
    notes: dict[str, str] = {}
    overridden: set[str] = set()
    for event in events:
        if event.event_type == REQUIREMENT_SCORED:
            requirement_id = event.event_data["requirement_id"]
            score = int(event.event_data["score"])
            agent.setdefault(requirement_id, score)
            effective[requirement_id] = score
        elif event.event_type == QUALITY_ASSESSMENT_COMPLETED:
            for item in event.event_data.get("requirement_scores") or []:
                requirement_id = item["requirement_id"]
                score = int(item["score"])
                agent.setdefault(requirement_id, score)
                effective[requirement_id] = score
        elif event.event_type == SCORE_OVERRIDDEN:
            requirement_id = event.event_data["requirement_id"]
            effective[requirement_id] = int(event.event_data["new_score"])
            notes[requirement_id] = str(event.event_data["justification"])
            overridden.add(requirement_id)
    lines = []
    for requirement in requirements:
        requirement_id = requirement.requirement_id
        if requirement_id not in effective:
            continue
        lines.append(
            _Line(
                requirement_id=requirement_id,
                weight=requirement.weight,
                is_threshold=requirement.is_threshold,
                agent_score=agent[requirement_id],
                effective=effective[requirement_id],
                overridden=requirement_id in overridden,
                note=notes.get(requirement_id),
            )
        )
    return lines


def score_row(line: _Line) -> dict:
    return {
        "requirement_id": line.requirement_id,
        "weight": line.weight,
        "is_threshold": line.is_threshold,
        "agent_score": line.agent_score,
        "score": line.effective,
        "override_score": line.effective if line.overridden else None,
        "override_justification": line.note if line.overridden else None,
    }


def _gate_event(lines: list[_Line], metadata: dict) -> NewEvent:
    quality, reason, failed_id = assess(
        [
            RequirementMark(line.requirement_id, line.weight, line.is_threshold, line.effective)
            for line in lines
        ]
    )
    if reason is None:
        return NewEvent(
            QUALITY_ASSESSMENT_COMPLETED,
            {
                "quality_score": format(quality, "f"),
                "requirement_scores": [
                    {
                        "requirement_id": line.requirement_id,
                        "score": line.effective,
                        "weight": line.weight,
                        "is_threshold": line.is_threshold,
                    }
                    for line in lines
                ],
            },
            metadata,
        )
    payload: dict = {"reason": reason}
    if failed_id:
        payload["failed_requirement_id"] = failed_id
    else:
        payload["quality_score"] = format(quality, "f")
    return NewEvent(BID_DISQUALIFIED, payload, metadata)


def _rerank(store, bids, state, organizer_id: str) -> None:
    qualified: list[QualifiedBid] = []
    prices: list[dict] = []
    for row in bids.comparison_for(state.tender_id):
        events = store.read_stream(row.bid_id)
        price = _price(events)
        if price is None:
            continue
        prices.append({"bid_id": row.bid_id, "amount": price})
        if not bid_is_qualified(events):
            continue
        lines = requirement_lines(state.requirements, events)
        quality, reason, _failed = assess(
            [
                RequirementMark(line.requirement_id, line.weight, line.is_threshold, line.effective)
                for line in lines
            ]
        )
        if reason is not None:
            continue
        qualified.append(
            QualifiedBid(
                bid_id=row.bid_id,
                quality=quality,
                price_amount=price,
                submitted_at=row.submitted_at,
            )
        )
    ranked = rank_bids(qualified, state.alpha)
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
    metadata = {"actor": organizer_id, "actor_role": "organizer"}
    store.append(
        state.tender_id,
        store.last_version(state.tender_id),
        FINAL_RANKING_CALCULATED,
        {"ranking": ranking},
        metadata,
    )
    bids.apply_revealed(ranking, prices)


def bid_is_qualified(events) -> bool:
    latest = None
    for event in events:
        if event.event_type == QUALITY_ASSESSMENT_COMPLETED:
            latest = True
        elif event.event_type == BID_DISQUALIFIED:
            latest = False
    return latest is True


def _assessed(events) -> bool:
    return any(
        event.event_type in (QUALITY_ASSESSMENT_COMPLETED, BID_DISQUALIFIED) for event in events
    )


def _price(events) -> int | None:
    amount = None
    for event in events:
        if event.event_type == PRICE_OFFER_SEALED:
            amount = int(event.event_data["price_amount"])
    return amount
