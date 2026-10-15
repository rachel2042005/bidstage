"""Organizer comparison, close, and price reveal."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.commands.close_tender import CloseTender, close_tender
from app.commands.reveal_prices import RevealPrices, reveal_prices
from app.domain.bid import DISQUALIFIED, QUALIFIED, SUBMITTED
from app.domain.tender import (
    DECIDED,
    FAILED,
    UNDER_EVALUATION,
    BidsNotAssessedError,
    NotTenderOwnerError,
    TenderNotFoundError,
)
from app.domain.user import ORGANIZER, DomainError
from app.queries.get_bid_comparison import get_bid_comparison
from app.queries.get_tender import get_tender_details
from app.routes.access import current_user_id, require_role
from app.routes.services import unit_of_work

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
    revealed = any(row.price_amount is not None for row in rows)
    return render_template(
        "tenders/comparison.html",
        details=details,
        rows=rows,
        revealed=revealed,
        can_reveal=details.tender.status == UNDER_EVALUATION and not revealed,
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


def _close_message(exc: DomainError) -> str:
    return "לא ניתן לסגור את המכרז במצב הזה."


def _reveal_message(exc: DomainError) -> str:
    if isinstance(exc, BidsNotAssessedError):
        return "לא ניתן לפתוח מחירים לפני שכל ההצעות הוערכו."
    return "לא ניתן לפתוח את המעטפות במצב הזה."
