"""Search and tender detail. FR-SRCH-1, FR-SRCH-2, FR-DET-1.

Free text matches the projected search document (name, region, requirement
descriptions). Suppliers are always scoped to open tenders.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.commands.publish_tender import RequirementSpec, publish_tender
from app.domain.tender import CONTENT, CULTURE, MUSIC, OPEN, SPORTS, VENUE
from app.domain.user import ORGANIZER, SUPPLIER
from app.projections.tenders import TenderSearch, compile_search_sql, rebuild_tenders
from app.queries.get_tender import get_tender_details
from app.queries.search_tenders import search_tenders
from main import create_app
from tests.test_tenders import NOW, _backend, _draft, _sign_in

import pytest


@pytest.fixture
def backend():
    return _backend()


@pytest.fixture
def client(backend):
    store, users, tenders = backend
    application = create_app(event_store=store, users=users, tenders=tenders)
    application.config["TESTING"] = True
    application.config["SECRET_KEY"] = "test-secret"
    return application.test_client()


def test_keyword_matches_a_requirement_description_and_not_another_tender(backend):
    store, _users, tenders = backend
    publish_tender(
        store,
        tenders,
        _draft(
            name="קונצרט",
            requirements=(RequirementSpec(MUSIC, 100, False, "תזמורת קאמרית"),),
            publish=True,
        ),
        now=NOW,
    )
    publish_tender(
        store,
        tenders,
        _draft(
            name="גמר",
            event_type=SPORTS,
            region="חיפה",
            requirements=(RequirementSpec(VENUE, 100, True, "אצטדיון עירוני"),),
            publish=True,
        ),
        now=NOW,
    )
    found = search_tenders(tenders, status=OPEN, keyword="אצטדיון")
    assert [row.name for row in found] == ["גמר"]


def test_keyword_tokens_are_conjunctive_with_event_type(backend):
    store, _users, tenders = backend
    publish_tender(
        store,
        tenders,
        _draft(
            name="ערב מחווה",
            event_type=CULTURE,
            requirements=(RequirementSpec(CONTENT, 100, False, "קטע מחווה ליוצר"),),
            publish=True,
        ),
        now=NOW,
    )
    publish_tender(
        store,
        tenders,
        _draft(
            name="גמר ליגה",
            event_type=SPORTS,
            region="חיפה",
            event_date=date(2027, 3, 1),
            requirements=(RequirementSpec(CONTENT, 100, False, "קטע מחווה לספורטאים"),),
            publish=True,
        ),
        now=NOW,
    )
    found = search_tenders(tenders, status=OPEN, keyword="מחווה", event_type=SPORTS)
    assert [row.name for row in found] == ["גמר ליגה"]
    both = search_tenders(tenders, status=OPEN, keyword="מחווה ספורטאים")
    assert [row.name for row in both] == ["גמר ליגה"]


def test_open_search_hides_drafts_even_when_the_keyword_matches(backend):
    store, _users, tenders = backend
    publish_tender(
        store,
        tenders,
        _draft(
            name="טיוטת אצטדיון",
            requirements=(RequirementSpec(VENUE, 40, True, "אצטדיון"),),
        ),
        now=NOW,
    )
    assert search_tenders(tenders, status=OPEN, keyword="אצטדיון") == []
    assert [row.name for row in search_tenders(tenders, keyword="אצטדיון")] == ["טיוטת אצטדיון"]


def test_keyword_still_matches_after_the_read_model_is_rebuilt(backend):
    store, _users, tenders = backend
    publish_tender(
        store,
        tenders,
        _draft(
            requirements=(RequirementSpec(VENUE, 100, True, "אולם ל-500 מוזמנים"),),
            publish=True,
        ),
        now=NOW,
    )
    rebuild_tenders(store, tenders)
    found = search_tenders(tenders, status=OPEN, keyword="500 מוזמנים")
    assert len(found) == 1


def test_tender_details_expose_deadline_alpha_weights_and_threshold(backend):
    """FR-DET-1."""
    store, _users, tenders = backend
    tender_id = publish_tender(
        store,
        tenders,
        _draft(
            requirements=(
                RequirementSpec(VENUE, 60, True, "אולם"),
                RequirementSpec(CONTENT, 40, False, "תוכן"),
            ),
            publish=True,
        ),
        now=NOW,
    )
    details = get_tender_details(tenders, tender_id)
    assert details is not None
    assert details.tender.deadline is not None
    assert details.tender.alpha == Decimal("0.6")
    assert details.weight_total == 100
    venue = details.requirements[0]
    assert venue.requirement_type == VENUE
    assert venue.weight == 60
    assert venue.is_threshold is True
    assert details.requirements[1].is_threshold is False


def test_search_sql_keeps_the_keyword_in_parameters():
    sql, params = compile_search_sql(
        TenderSearch(status=OPEN, event_type=CULTURE, keyword="אולם 100%")
    )
    assert "אולם" not in sql
    assert "100%" not in sql
    assert "status = ?" in sql
    assert "event_type = ?" in sql
    assert sql.count("search_text LIKE ?") == 2
    assert "ORDER BY event_date, name" in sql
    assert params[0] == OPEN
    assert params[1] == CULTURE
    assert "%אולם%" in params
    assert "%100[%]%" in params


def test_supplier_keyword_search_and_detail_page(client, backend):
    store, _users, tenders = backend
    _sign_in(client, ORGANIZER)
    tender_id = publish_tender(
        store,
        tenders,
        _draft(
            name="טקס פרסים",
            requirements=(
                RequirementSpec(VENUE, 60, True, "אולם ל-500"),
                RequirementSpec(CONTENT, 40, False, "מופע מחווה"),
            ),
            publish=True,
        ),
        now=NOW,
    )
    publish_tender(
        store,
        tenders,
        _draft(
            name="גמר כדורגל",
            event_type=SPORTS,
            region="חיפה",
            requirements=(RequirementSpec(VENUE, 100, True, "אצטדיון"),),
            publish=True,
        ),
        now=NOW,
    )
    _sign_in(client, SUPPLIER, user_id="sup-1")
    listing = client.get("/supplier/?q=מחווה")
    body = listing.get_data(as_text=True)
    assert listing.status_code == 200
    assert "טקס פרסים" in body
    assert "גמר כדורגל" not in body
    assert "פתוח להצעות" in body

    forced = client.get("/supplier/?q=אצטדיון&status=draft")
    assert "טיוטת" not in forced.get_data(as_text=True)
    assert "גמר כדורגל" in forced.get_data(as_text=True)

    detail = client.get(f"/supplier/tenders/{tender_id}")
    page = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "קריטריוני הערכה" in page
    assert "מועד אחרון להגשה" in page
    assert "60%" in page
    assert "תנאי סף" in page
    assert "40%" in page
