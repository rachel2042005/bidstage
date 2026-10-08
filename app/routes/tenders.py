"""Organizer tender drafting and publication, and supplier read of open tenders."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.commands.add_requirement import AddRequirement, add_requirement
from app.commands.publish_tender import PublishTender, RequirementSpec, publish_tender
from app.domain.tender import (
    CONTENT,
    CULTURE,
    DRAFT,
    EVENT_TYPES,
    EXPERIENCE,
    HOST,
    MUSIC,
    OPEN,
    REQUIREMENT_TYPES,
    SPORTS,
    VENUE,
    DomainError,
    InvalidAlphaError,
    InvalidBudgetError,
    InvalidEventTypeError,
    InvalidRequirementTypeError,
    InvalidTenderError,
    InvalidWeightError,
    NoRequirementsError,
    NotTenderOwnerError,
    PastDeadlineError,
    TenderAlreadyPublishedError,
    TenderNotFoundError,
    WeightSumError,
    parse_weight,
)
from app.domain.user import ORGANIZER, SUPPLIER
from app.queries.get_tender import get_tender_details
from app.routes.access import current_user_id, require_role
from app.routes.formatting import ISRAEL
from app.routes.services import unit_of_work

bp = Blueprint("tenders", __name__)

EVENT_TYPE_LABELS = {CULTURE: "תרבות", SPORTS: "ספורט"}
REQUIREMENT_TYPE_LABELS = {
    VENUE: "מקום",
    CONTENT: "תוכן",
    HOST: "מנחה",
    MUSIC: "מוזיקה",
    EXPERIENCE: "חוויה",
}
STATUS_LABELS = {
    DRAFT: "טיוטה",
    OPEN: "פתוח להצעות",
}

_BLANK_ROW = {
    "requirement_type": "",
    "weight": "",
    "is_threshold": "0",
    "description": "",
}


@bp.get("/organizer/tenders/new")
@require_role(ORGANIZER)
def new_tender():
    return render_template("tenders/new.html", rows=[dict(_BLANK_ROW)], form={})


@bp.post("/organizer/tenders/new")
@require_role(ORGANIZER)
def create_tender():
    organizer_id = current_user_id() or ""
    try:
        command = _command_from_form(request.form, organizer_id)
        with unit_of_work() as (store, _users, tenders):
            tender_id = publish_tender(store, tenders, command)
    except DomainError as exc:
        _flash_domain_error(exc)
        return render_template(
            "tenders/new.html",
            rows=_posted_rows(request.form),
            form=request.form,
        ), 400
    if command.publish:
        flash("המכרז פורסם.", "success")
    else:
        flash("הטיוטה נשמרה. אפשר להוסיף דרישות ולפרסם כשהמשקלות מסתכמים ב-100.", "success")
    return redirect(url_for("tenders.organizer_tender", tender_id=tender_id))


@bp.get("/organizer/tenders/<tender_id>")
@require_role(ORGANIZER)
def organizer_tender(tender_id: str):
    details = _details_for_owner(tender_id)
    return render_template(
        "tenders/detail.html",
        details=details,
        can_edit=details.tender.status == DRAFT,
        back_url=url_for("pages.organizer_home"),
        back_label="חזרה למכרזים שלי",
    )


@bp.post("/organizer/tenders/<tender_id>/requirements")
@require_role(ORGANIZER)
def add_tender_requirement(tender_id: str):
    organizer_id = current_user_id() or ""
    try:
        command = AddRequirement(
            tender_id=tender_id,
            organizer_id=organizer_id,
            requirement_type=request.form.get("requirement_type", ""),
            weight=parse_weight(request.form.get("weight", "")),
            is_threshold=request.form.get("is_threshold") == "1",
            description=request.form.get("description", ""),
        )
        with unit_of_work() as (store, _users, tenders):
            add_requirement(store, tenders, command)
    except NotTenderOwnerError:
        abort(403)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        _flash_domain_error(exc)
        return redirect(url_for("tenders.organizer_tender", tender_id=tender_id))
    flash("הדרישה נוספה.", "success")
    return redirect(url_for("tenders.organizer_tender", tender_id=tender_id))


@bp.post("/organizer/tenders/<tender_id>/publish")
@require_role(ORGANIZER)
def publish_existing(tender_id: str):
    organizer_id = current_user_id() or ""
    try:
        with unit_of_work() as (store, _users, tenders):
            publish_tender(
                store,
                tenders,
                PublishTender(organizer_id=organizer_id, tender_id=tender_id, publish=True),
            )
    except NotTenderOwnerError:
        abort(403)
    except TenderNotFoundError:
        abort(404)
    except DomainError as exc:
        _flash_domain_error(exc)
        return redirect(url_for("tenders.organizer_tender", tender_id=tender_id))
    flash("המכרז פורסם.", "success")
    return redirect(url_for("tenders.organizer_tender", tender_id=tender_id))


@bp.get("/supplier/tenders/<tender_id>")
@require_role(SUPPLIER)
def supplier_tender(tender_id: str):
    with unit_of_work() as (_store, _users, tenders):
        details = get_tender_details(tenders, tender_id)
    if details is None:
        abort(404)
    if details.tender.status != OPEN:
        abort(403)
    return render_template(
        "tenders/detail.html",
        details=details,
        can_edit=False,
        back_url=url_for("pages.supplier_home"),
        back_label="חזרה למכרזים הפתוחים",
    )


def _details_for_owner(tender_id: str):
    with unit_of_work() as (_store, _users, tenders):
        details = get_tender_details(tenders, tender_id)
    if details is None:
        abort(404)
    if details.tender.organizer_id != current_user_id():
        abort(403)
    return details


def _command_from_form(form, organizer_id: str) -> PublishTender:
    return PublishTender(
        organizer_id=organizer_id,
        name=form.get("name", ""),
        event_type=form.get("event_type", ""),
        event_date=_parse_date(form.get("event_date", "")),
        region=form.get("region", ""),
        budget_amount=_parse_budget(form.get("budget", "")),
        deadline=_parse_deadline(form.get("deadline", "")),
        alpha=form.get("alpha", ""),
        requirements=_parse_requirement_rows(form),
        publish=form.get("intent") == "publish",
    )


def _parse_requirement_rows(form) -> tuple[RequirementSpec, ...]:
    types = form.getlist("req_type")
    weights = form.getlist("req_weight")
    thresholds = form.getlist("req_threshold")
    descriptions = form.getlist("req_description")
    if not (len(types) == len(weights) == len(thresholds) == len(descriptions)):
        raise InvalidTenderError("requirements are incomplete")
    specs: list[RequirementSpec] = []
    for req_type, weight, threshold, description in zip(
        types, weights, thresholds, descriptions, strict=True
    ):
        if not any(part.strip() for part in (req_type, weight, description)):
            continue
        if req_type not in REQUIREMENT_TYPES:
            raise InvalidRequirementTypeError(req_type)
        if threshold not in {"0", "1"}:
            raise InvalidTenderError("threshold flag is required")
        specs.append(
            RequirementSpec(
                requirement_type=req_type,
                weight=parse_weight(weight),
                is_threshold=threshold == "1",
                description=description,
            )
        )
    return tuple(specs)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidTenderError("event_date is required") from exc


def _parse_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidTenderError("deadline is required") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ISRAEL)
    return parsed.astimezone(timezone.utc)


def _parse_budget(value: str) -> int:
    cleaned = value.strip().replace(",", "").replace("₪", "").replace(" ", "")
    if not cleaned:
        raise InvalidBudgetError("budget is required")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise InvalidBudgetError("budget is required") from exc
    minor = amount * 100
    if minor != minor.to_integral_value() or minor <= 0:
        raise InvalidBudgetError("budget is required")
    return int(minor)


def _posted_rows(form) -> list[dict[str, str]]:
    types = form.getlist("req_type")
    weights = form.getlist("req_weight")
    thresholds = form.getlist("req_threshold")
    descriptions = form.getlist("req_description")
    rows = [
        {
            "requirement_type": req_type,
            "weight": weight,
            "is_threshold": threshold,
            "description": description,
        }
        for req_type, weight, threshold, description in zip(
            types, weights, thresholds, descriptions
        )
    ]
    return rows or [dict(_BLANK_ROW)]


def _flash_domain_error(exc: DomainError) -> None:
    if isinstance(exc, WeightSumError):
        flash(f"המשקלות מסתכמים ב-{exc.total} ולא ב-100.", "danger")
    elif isinstance(exc, PastDeadlineError):
        flash("מועד ההגשה חייב להיות בעתיד.", "danger")
    elif isinstance(exc, NoRequirementsError):
        flash("לא ניתן לפרסם מכרז בלי דרישות.", "danger")
    elif isinstance(exc, InvalidEventTypeError):
        flash("יש לבחור סוג אירוע: תרבות או ספורט.", "danger")
    elif isinstance(exc, InvalidRequirementTypeError):
        flash("סוג הדרישה אינו מוכר.", "danger")
    elif isinstance(exc, InvalidWeightError):
        flash("משקל דרישה חייב להיות מספר שלם בין 1 ל-100.", "danger")
    elif isinstance(exc, InvalidAlphaError):
        flash("α חייב להיות בין 0 ל-1, עד שתי ספרות אחרי הנקודה.", "danger")
    elif isinstance(exc, InvalidBudgetError):
        flash("יש להזין תקציב חיובי בשקלים.", "danger")
    elif isinstance(exc, TenderAlreadyPublishedError):
        flash("אפשר להוסיף דרישות רק לטיוטה, והמכרז כבר פורסם.", "danger")
    else:
        flash("הנתונים שהוזנו אינם תקינים.", "danger")
