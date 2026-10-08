"""Role homes. Lists are read from rm_tender_search (FR-DASH-5, FR-SRCH-1)."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, render_template, request

from app.domain.tender import EVENT_TYPES, OPEN
from app.domain.user import ORGANIZER, SUPPLIER
from app.queries.search_tenders import search_tenders
from app.routes.access import current_user_id, require_role
from app.routes.services import unit_of_work

bp = Blueprint("pages", __name__)


@bp.get("/")
def home():
    return render_template("home.html")


@bp.get("/organizer/")
@require_role(ORGANIZER)
def organizer_home():
    with unit_of_work() as (_store, _users, tenders):
        rows = search_tenders(tenders, organizer_id=current_user_id())
    return render_template(
        "organizer/home.html",
        tenders=rows,
        detail_endpoint="tenders.organizer_tender",
        show_status=True,
        empty_message="עדיין אין מכרזים. אפשר לפתוח טיוטה חדשה.",
    )


@bp.get("/supplier/")
@require_role(SUPPLIER)
def supplier_home():
    event_type = request.args.get("event_type", "").strip()
    region = request.args.get("region", "").strip()
    event_date_from = _optional_date(request.args.get("event_date_from", ""))
    event_date_to = _optional_date(request.args.get("event_date_to", ""))
    with unit_of_work() as (_store, _users, tenders):
        rows = search_tenders(
            tenders,
            status=OPEN,
            event_type=event_type if event_type in EVENT_TYPES else None,
            region=region or None,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
        )
    return render_template(
        "supplier/home.html",
        tenders=rows,
        detail_endpoint="tenders.supplier_tender",
        show_status=False,
        empty_message="אין מכרזים פתוחים התואמים לסינון.",
        filters={
            "event_type": event_type,
            "region": region,
            "event_date_from": request.args.get("event_date_from", ""),
            "event_date_to": request.args.get("event_date_to", ""),
        },
    )


def _optional_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
