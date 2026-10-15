"""Organizer semantic search over bids (FR-SRCH-4, FR-SRCH-6)."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request

from app.domain.tender import NotTenderOwnerError, TenderNotFoundError
from app.domain.user import ORGANIZER
from app.queries.get_dashboard import get_dashboard
from app.queries.search_bids import search_organizer_bids
from app.routes.access import current_user_id, require_role
from app.routes.services import unit_of_work

bp = Blueprint("bid_search", __name__)

_PROMPT = "הקלידו טקסט לחיפוש"
_EMPTY = "לא נמצאו הצעות תואמות"
_UNAVAILABLE = "החיפוש אינו זמין כרגע"


class UnavailableBidSearch:
    """Stand-in used when no bid search port is configured."""

    def search_bids(self, query: str, tender_ids: list[str], top_k: int = 5):
        raise RuntimeError("bid search is not configured")


@bp.get("/organizer/search")
@require_role(ORGANIZER)
def organizer_search():
    query = request.args.get("q", "")
    tender_id = request.args.get("tender_id", "").strip() or None
    organizer_id = current_user_id() or ""
    searcher = current_app.extensions.get("bid_search") or UnavailableBidSearch()
    try:
        with unit_of_work() as (_store, _users, tenders, bids):
            result = search_organizer_bids(
                tenders,
                bids,
                searcher,
                organizer_id=organizer_id,
                query=query,
                tender_id=tender_id,
            )
            dashboard = get_dashboard(tenders, organizer_id)
    except TenderNotFoundError:
        abort(404)
    except NotTenderOwnerError:
        abort(403)
    options = list(dashboard.under_evaluation) + list(dashboard.decided) + list(dashboard.failed)
    notice = _notice(query, result.unavailable, result.hits)
    return render_template(
        "organizer/search.html",
        query=query.strip(),
        selected_tender_id=tender_id or "",
        tenders=options,
        hits=result.hits,
        notice=notice,
    )


def _notice(query: str, unavailable: bool, hits) -> str | None:
    if not query.strip():
        return _PROMPT
    if unavailable:
        return _UNAVAILABLE
    if not hits:
        return _EMPTY
    return None
