"""Organizer comparison, close, and price reveal."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from app.commands.close_tender import CloseTender, close_tender
from app.commands.override_score import OverrideScore, override_score
from app.commands.reveal_prices import RevealPrices, reveal_prices
from app.commands.select_winner import SelectWinner, select_winner
from app.domain.bid import DISQUALIFIED, QUALIFIED, SUBMITTED
from app.domain.tender import (
    DECIDED,
    FAILED,
    UNDER_EVALUATION,
    BidsNotAssessedError,
    EmptyJustificationError,
    NotTenderOwnerError,
    ScoreOutOfRangeError,
    TenderNotFoundError,
)
from app.domain.user import ORGANIZER, DomainError
from app.queries.get_bid_comparison import get_bid_comparison
from app.queries.get_tender import get_tender_details
from app.routes.access import current_user_id, require_role
from app.routes.services import unit_of_work
from app.routes.tenders import REQUIREMENT_TYPE_LABELS

bp = Blueprint("comparison", __name__)

BID_STATUS_LABELS = {
    SUBMITTED: "הוגשה",
    QUALIFIED: "כשירה",
    DISQUALIFIED: "נפסלה",
}
_COMPARISON_STATUSES = {UNDER_EVALUATION, FAILED, DECIDED}


@bp.get("/organizer/tenders/<tender_id>/comparison")
@require_role(ORGANIZER)
def comparison(tender_id: str):
    sort = request.args.get("sort", "").strip()
    if sort not in {"quality", "price", "final"}:
        sort = ""
    with unit_of_work() as (_store, _users, tenders, bids):
        details = get_tender_details(tenders, tender_id)
        if details is None:
            abort(404)
        if details.tender.organizer_id != current_user_id():
            abort(403)
        if details.tender.status not in _COMPARISON_STATUSES:
            abort(403)
        rows = get_bid_comparison(bids, tender_id, sort=sort or None)
        scores = {row.bid_id: bids.scores_for(row.bid_id) for row in rows}
    revealed = any(row.price_amount is not None for row in rows)
    labels = {
        item.requirement_id: REQUIREMENT_TYPE_LABELS.get(item.requirement_type, item.requirement_type)
        for item in details.requirements
    }
    return render_template(
        "tenders/comparison.html",
        details=details,
        rows=rows,
        scores=scores,
        requirement_labels=labels,
        revealed=revealed,
        can_reveal=details.tender.status == UNDER_EVALUATION and not revealed,
        can_decide=details.tender.status == UNDER_EVALUATION and revealed,
        sort=sort,
        bid_status_labels=BID_STATUS_LABELS,
    )


@bp.post("/organizer/tenders/<tender_id>/close")
@require_role(ORGANIZER)
def close(tender_id: str):
    try:
        with unit_of_work() as (store, _users, tenders, bids):
            close_tender(
                store,
                tenders,
                bids,
                CloseTender(
                    tender_id=tender_id,
                    organizer_id=current_user_id() or "",
                    explicit=True,
                ),
            )
    except NotTenderOwnerError:
        abort(403)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        flash(_close_message(exc), "danger")
        return redirect(url_for("tenders.organizer_tender", tender_id=tender_id))
    flash("המכרז נסגר להצעות.", "success")
    return redirect(url_for("comparison.comparison", tender_id=tender_id))


@bp.post("/organizer/tenders/<tender_id>/reveal")
@require_role(ORGANIZER)
def reveal(tender_id: str):
    try:
        with unit_of_work() as (store, _users, tenders, bids):
            reveal_prices(
                store,
                tenders,
                bids,
                RevealPrices(tender_id=tender_id, organizer_id=current_user_id() or ""),
            )
    except NotTenderOwnerError:
        abort(403)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        flash(_reveal_message(exc), "danger")
        return redirect(url_for("comparison.comparison", tender_id=tender_id))
    flash("המעטפות נפתחו.", "success")
    return redirect(url_for("comparison.comparison", tender_id=tender_id))


@bp.post("/organizer/tenders/<tender_id>/bids/<bid_id>/score")
@require_role(ORGANIZER)
def override(tender_id: str, bid_id: str):
    try:
        score = _parse_score(request.form.get("new_score", ""))
        with unit_of_work() as (store, _users, tenders, bids):
            override_score(
                store,
                tenders,
                bids,
                OverrideScore(
                    tender_id=tender_id,
                    bid_id=bid_id,
                    requirement_id=request.form.get("requirement_id", ""),
                    new_score=score,
                    justification=request.form.get("justification", ""),
                    organizer_id=current_user_id() or "",
                ),
            )
    except NotTenderOwnerError:
        abort(403)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        flash(_override_message(exc), "danger")
        return redirect(url_for("comparison.comparison", tender_id=tender_id))
    flash("הציון עודכן והדירוג חושב מחדש.", "success")
    return redirect(url_for("comparison.comparison", tender_id=tender_id))


@bp.post("/organizer/tenders/<tender_id>/winner")
@require_role(ORGANIZER)
def award(tender_id: str):
    notices = ()
    try:
        with unit_of_work() as (store, users, tenders, bids):
            notices = select_winner(
                store,
                tenders,
                bids,
                users,
                SelectWinner(
                    tender_id=tender_id,
                    bid_id=request.form.get("bid_id", ""),
                    organizer_id=current_user_id() or "",
                ),
            )
    except NotTenderOwnerError:
        abort(403)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        flash(_award_message(exc), "danger")
        return redirect(url_for("comparison.comparison", tender_id=tender_id))
    _deliver(notices)
    flash("הזוכה נבחר.", "success")
    return redirect(url_for("comparison.comparison", tender_id=tender_id))


def _deliver(notices) -> None:
    mailer = current_app.extensions.get("bid_notifier")
    if mailer is None:
        from app.notifications.gmail import notifier_from_environment

        mailer = notifier_from_environment()
    send = getattr(mailer, "send", None)
    if send is None:
        return
    for notice in notices:
        try:
            send(notice.to_email, notice.subject, notice.body)
        except Exception:
            continue


def _parse_score(value: str) -> int:
    cleaned = value.strip()
    if not cleaned.isdigit():
        raise ScoreOutOfRangeError("score must be an integer from 0 to 10")
    score = int(cleaned)
    if score > 10:
        raise ScoreOutOfRangeError("score must be an integer from 0 to 10")
    return score


def _override_message(exc: DomainError) -> str:
    if isinstance(exc, EmptyJustificationError):
        return "יש לכתוב נימוק לדריסת הציון."
    if isinstance(exc, ScoreOutOfRangeError):
        return "הציון חייב להיות מספר שלם בין 0 ל-10."
    return "לא ניתן לדרוס את הציון במצב הזה."


def _award_message(exc: DomainError) -> str:
    return "לא ניתן לבחור זוכה במצב הזה."


def _close_message(exc: DomainError) -> str:
    return "לא ניתן לסגור את המכרז במצב הזה."


def _reveal_message(exc: DomainError) -> str:
    if isinstance(exc, BidsNotAssessedError):
        return "לא ניתן לפתוח מחירים לפני שכל ההצעות הוערכו."
    return "לא ניתן לפתוח את המעטפות במצב הזה."
