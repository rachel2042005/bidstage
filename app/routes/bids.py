"""Supplier bid submission. Organizers and anonymous users never reach the form."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from app.commands.submit_bid import BidResponse, SubmitBid, submit_bid
from app.domain.bid import (
    DISQUALIFIED,
    QUALIFIED,
    SUBMITTED,
    ActiveBidExistsError,
    DeadlinePassedError,
    IncompleteBidError,
    InvalidPriceError,
    TenderNotOpenError,
)
from app.domain.tender import TenderNotFoundError
from app.domain.user import SUPPLIER, DomainError
from app.queries.get_bid import list_supplier_bids
from app.queries.get_tender import get_tender_details
from app.routes.tenders import REQUIREMENT_TYPE_LABELS
from app.routes.access import current_user_id, require_role
from app.routes.services import unit_of_work

bp = Blueprint("bids", __name__)

BID_STATUS_LABELS = {
    SUBMITTED: "הוגשה",
    QUALIFIED: "כשירה",
    DISQUALIFIED: "נפסלה",
}


@bp.get("/supplier/bids")
@require_role(SUPPLIER)
def my_bids():
    supplier_id = current_user_id() or ""
    with unit_of_work() as (_store, _users, _tenders, bids):
        rows = list_supplier_bids(bids, supplier_id)
    return render_template(
        "supplier/bids.html",
        bids=rows,
        bid_status_labels=BID_STATUS_LABELS,
    )


@bp.get("/supplier/bids/<bid_id>")
@require_role(SUPPLIER)
def bid_detail(bid_id: str):
    supplier_id = current_user_id() or ""
    with unit_of_work() as (_store, _users, tenders, bids):
        assessment = bids.assessment(bid_id)
        if assessment is None:
            abort(404)
        own = next((row for row in bids.for_supplier(supplier_id) if row.bid_id == bid_id), None)
        if own is None:
            abort(403)
        details = get_tender_details(tenders, assessment.tender_id)
        scores = bids.scores_for(bid_id)
        ranking = next(
            (row for row in bids.comparison_for(assessment.tender_id) if row.bid_id == bid_id),
            None,
        )
    labels = {}
    if details is not None:
        labels = {
            item.requirement_id: REQUIREMENT_TYPE_LABELS.get(item.requirement_type, item.requirement_type)
            for item in details.requirements
        }
    return render_template(
        "supplier/bid.html",
        bid=own,
        assessment=assessment,
        scores=scores,
        ranking=ranking,
        requirement_labels=labels,
        revealed=ranking is not None and ranking.price_amount is not None,
        bid_status_labels=BID_STATUS_LABELS,
    )


@bp.post("/supplier/tenders/<tender_id>/bids")
@require_role(SUPPLIER)
def submit(tender_id: str):
    supplier_id = current_user_id() or ""
    try:
        price_amount = _parse_price_shekels(request.form.get("price", ""))
        responses = tuple(
            BidResponse(key.removeprefix("answer_"), request.form.get(key, ""))
            for key in request.form
            if key.startswith("answer_")
        )
        command = SubmitBid(
            tender_id=tender_id,
            supplier_id=supplier_id,
            supplier_email=session.get("email") or "",
            concept=request.form.get("concept", ""),
            responses=responses,
            price_amount=price_amount,
            supplier_display_name=session.get("display_name") or "",
        )
        with unit_of_work() as (store, _users, tenders, bids):
            submit_bid(store, bids, command, notifier=_notifier(), tenders=tenders)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        _flash_bid_error(exc)
        return redirect(url_for("tenders.supplier_tender", tender_id=tender_id))
    flash("ההצעה התקבלה.", "success")
    return redirect(url_for("bids.my_bids"))


def _notifier():
    injected = current_app.extensions.get("bid_notifier")
    if injected is not None:
        return injected
    from app.notifications.gmail import notifier_from_environment

    return notifier_from_environment()


def _parse_price_shekels(value: str) -> int:
    cleaned = value.strip().replace(",", "").replace("₪", "").replace(" ", "")
    if not cleaned:
        raise InvalidPriceError("price is required")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise InvalidPriceError("price is required") from exc
    minor = amount * 100
    if minor != minor.to_integral_value() or minor <= 0:
        raise InvalidPriceError("price is required")
    return int(minor)


def _flash_bid_error(exc: DomainError) -> None:
    if isinstance(exc, DeadlinePassedError):
        flash("מועד ההגשה עבר.", "danger")
    elif isinstance(exc, ActiveBidExistsError):
        flash("כבר הגשתם הצעה למכרז זה.", "danger")
    elif isinstance(exc, IncompleteBidError):
        flash("יש למלא תיאור קונספט ומענה לכל דרישה.", "danger")
    elif isinstance(exc, InvalidPriceError):
        flash("יש להזין מחיר חיובי בשקלים.", "danger")
    elif isinstance(exc, TenderNotOpenError):
        flash("לא ניתן להגיש הצעה למכרז שאינו פתוח.", "danger")
    else:
        flash("הנתונים שהוזנו אינם תקינים.", "danger")
