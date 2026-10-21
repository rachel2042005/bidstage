"""Revise a draft, then publish it. FR-ENT-9.

Seams: ReviseTenderDetails, the organizer tender page, and PublishTender.
"""

from __future__ import annotations

import pytest

from app.commands.publish_tender import publish_tender
from app.commands.revise_tender_details import ReviseTenderDetails, revise_tender_details
from app.domain.tender import CULTURE, OPEN, VENUE
from app.domain.user import ORGANIZER
from app.events.event_types import TENDER_CREATED, TENDER_DETAILS_REVISED
from app.queries.get_dashboard import get_dashboard
from main import create_app
from tests.test_tenders import NOW, ORGANIZER_ID, _backend, _draft, _sign_in


def test_revising_a_draft_appends_the_revision_and_the_page_reads_the_new_name():
    store, _users, tenders = _backend()
    tender_id = publish_tender(store, tenders, _draft(name="טקס פרסים"), now=NOW)
    revise_tender_details(
        store,
        tenders,
        ReviseTenderDetails(
            tender_id=tender_id,
            organizer_id=ORGANIZER_ID,
            name="קונצרט",
            event_type=CULTURE,
            region="חיפה",
        ),
        now=NOW,
    )
    events = store.read_stream(tender_id)
    assert [event.event_type for event in events] == [TENDER_CREATED, TENDER_DETAILS_REVISED]
    assert events[1].event_data["name"] == "קונצרט"
    assert events[1].event_data["region"] == "חיפה"
    summary = tenders.get(tender_id)
    assert summary is not None
    assert summary.name == "קונצרט"
    assert summary.region == "חיפה"
    assert summary.status == "draft"
    assert [item.name for item in get_dashboard(tenders, ORGANIZER_ID).draft] == ["קונצרט"]


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


def test_the_owner_revises_a_draft_from_its_page(client):
    _sign_in(client, ORGANIZER)
    created = client.post(
        "/organizer/tenders/new",
        data={"name": "טקס פרסים", "event_type": CULTURE, "intent": "draft"},
        follow_redirects=False,
    )
    assert created.status_code == 302
    page = client.get(created.headers["Location"])
    assert 'value="טקס פרסים"' in page.get_data(as_text=True)
    revised = client.post(
        created.headers["Location"].rstrip("/") + "/details",
        data={"name": "קונצרט", "event_type": CULTURE, "region": "חיפה"},
        follow_redirects=False,
    )
    assert revised.status_code == 302
    body = client.get(created.headers["Location"]).get_data(as_text=True)
    assert 'value="קונצרט"' in body
    assert 'value="חיפה"' in body


def test_another_organizer_cannot_revise_a_draft(client, backend):
    _sign_in(client, ORGANIZER)
    created = client.post(
        "/organizer/tenders/new",
        data={"name": "טקס פרסים", "event_type": CULTURE, "intent": "draft"},
        follow_redirects=False,
    )
    target = created.headers["Location"].rstrip("/") + "/details"
    _sign_in(client, ORGANIZER, user_id="org-2")
    response = client.post(
        target,
        data={"name": "קונצרט", "event_type": CULTURE},
    )
    assert response.status_code == 403
    assert "קונצרט".encode() not in response.data
    store, _users, _tenders = backend
    assert TENDER_DETAILS_REVISED not in [event.event_type for event in store.read_all()]


def test_a_published_tender_has_no_edit_form_and_a_revision_is_refused(client, backend):
    _sign_in(client, ORGANIZER)
    created = client.post(
        "/organizer/tenders/new",
        data={
            "name": "טקס פרסים",
            "event_type": CULTURE,
            "event_date": "2026-12-01",
            "region": "תל אביב",
            "budget": "500000",
            "deadline": "2026-11-01T18:00",
            "alpha": "0.6",
            "req_type": [VENUE],
            "req_weight": ["100"],
            "req_threshold": ["1"],
            "req_description": ["אולם"],
            "intent": "publish",
        },
        follow_redirects=False,
    )
    page = client.get(created.headers["Location"])
    assert "שמירת שינויים" not in page.get_data(as_text=True)
    refused = client.post(
        created.headers["Location"].rstrip("/") + "/details",
        data={"name": "קונצרט", "event_type": CULTURE},
        follow_redirects=True,
    )
    assert "אפשר לערוך רק טיוטה." in refused.get_data(as_text=True)
    store, _users, tenders = backend
    assert TENDER_DETAILS_REVISED not in [event.event_type for event in store.read_all()]
    assert tenders.get(created.headers["Location"].rstrip("/").split("/")[-1]).status == OPEN


def test_a_revised_draft_can_be_published(client):
    _sign_in(client, ORGANIZER)
    created = client.post(
        "/organizer/tenders/new",
        data={"name": "טקס פרסים", "event_type": CULTURE, "intent": "draft"},
        follow_redirects=False,
    )
    tender_url = created.headers["Location"]
    saved = client.post(
        tender_url.rstrip("/") + "/details",
        data={
            "name": "קונצרט",
            "event_type": CULTURE,
            "event_date": "2026-12-01",
            "region": "חיפה",
            "budget": "500000",
            "deadline": "2026-11-01T18:00",
            "alpha": "0.6",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 302
    client.post(
        tender_url.rstrip("/") + "/requirements",
        data={
            "requirement_type": VENUE,
            "weight": "100",
            "is_threshold": "1",
            "description": "אולם",
        },
    )
    published = client.post(tender_url.rstrip("/") + "/publish", follow_redirects=True)
    body = published.get_data(as_text=True)
    assert "קונצרט" in body
    assert "פתוח להצעות" in body
    assert "שמירת שינויים" not in body
