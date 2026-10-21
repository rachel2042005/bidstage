"""Draft flexibility and date rules. FR-ENT-1, FR-ENT-4.

Seams: PublishTender, the organizer tender form, and the dashboard.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.commands.publish_tender import PublishTender, RequirementSpec, publish_tender
from app.domain.tender import (
    CULTURE,
    DeadlineAfterEventError,
    InvalidBudgetError,
    InvalidTenderError,
    PastDeadlineError,
    PastEventDateError,
    VENUE,
    israel_today,
)
from app.domain.user import ORGANIZER
from app.events.event_types import TENDER_CREATED, TENDER_PUBLISHED
from app.queries.get_dashboard import get_dashboard
from main import create_app
from tests.test_tenders import NOW, ORGANIZER_ID, _backend, _sign_in


def test_draft_with_only_name_and_type_is_stored():
    store, _users, tenders = _backend()
    tender_id = publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=ORGANIZER_ID,
            name="טקס פרסים",
            event_type=CULTURE,
            publish=False,
        ),
        now=NOW,
    )
    events = store.read_stream(tender_id)
    assert [event.event_type for event in events] == [TENDER_CREATED]
    data = events[0].event_data
    assert data["name"] == "טקס פרסים"
    assert data["event_type"] == CULTURE
    assert data["alpha"] == "0.6"
    assert data.get("deadline") is None
    assert data.get("event_date") is None
    assert data.get("estimated_budget") is None
    assert data.get("region") in (None, "")
    draft = get_dashboard(tenders, ORGANIZER_ID).draft
    assert [item.tender_id for item in draft] == [tender_id]
    assert draft[0].name == "טקס פרסים"
    assert draft[0].deadline is None


def test_a_draft_rejects_a_past_date_a_late_deadline_and_a_zero_budget():
    store, _users, tenders = _backend()
    past_deadline = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(PastDeadlineError):
        publish_tender(
            store,
            tenders,
            PublishTender(
                organizer_id=ORGANIZER_ID,
                name="טקס פרסים",
                event_type=CULTURE,
                deadline=past_deadline,
            ),
            now=NOW,
        )
    with pytest.raises(PastEventDateError):
        publish_tender(
            store,
            tenders,
            PublishTender(
                organizer_id=ORGANIZER_ID,
                name="טקס פרסים",
                event_type=CULTURE,
                event_date=date(2026, 10, 7),
            ),
            now=NOW,
        )
    with pytest.raises(DeadlineAfterEventError):
        publish_tender(
            store,
            tenders,
            PublishTender(
                organizer_id=ORGANIZER_ID,
                name="טקס פרסים",
                event_type=CULTURE,
                event_date=date(2026, 12, 1),
                deadline=datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc),
            ),
            now=NOW,
        )
    with pytest.raises(InvalidBudgetError):
        publish_tender(
            store,
            tenders,
            PublishTender(
                organizer_id=ORGANIZER_ID,
                name="טקס פרסים",
                event_type=CULTURE,
                budget_amount=0,
            ),
            now=NOW,
        )
    assert store.read_all() == []


@pytest.fixture
def client(backend):
    store, users, tenders = backend
    application = create_app(event_store=store, users=users, tenders=tenders)
    application.config["TESTING"] = True
    application.config["SECRET_KEY"] = "test-secret"
    return application.test_client()


@pytest.fixture
def backend():
    return _backend()


def test_sparse_draft_form_saves_and_the_dashboard_says_the_deadline_is_unset(client):
    _sign_in(client, ORGANIZER)
    response = client.post(
        "/organizer/tenders/new",
        data={"name": "טקס פרסים", "event_type": CULTURE, "intent": "draft"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    home = client.get("/organizer/")
    body = home.get_data(as_text=True)
    assert "טקס פרסים" in body
    assert "טרם נקבע" in body
    assert 'data-deadline="' not in body
    detail = client.get(response.headers["Location"])
    page = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "טקס פרסים" in page
    assert "טרם נקבע" in page


def test_a_draft_may_store_a_requirement_without_a_description():
    store, _users, tenders = _backend()
    tender_id = publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=ORGANIZER_ID,
            name="טקס פרסים",
            event_type=CULTURE,
            requirements=(RequirementSpec(VENUE, 40, False, "  "),),
        ),
        now=NOW,
    )
    added = store.read_stream(tender_id)[1]
    assert added.event_data["description"] == ""


def test_publishing_an_incomplete_draft_appends_nothing():
    store, _users, tenders = _backend()
    tender_id = publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=ORGANIZER_ID,
            name="טקס פרסים",
            event_type=CULTURE,
            event_date=date(2026, 12, 1),
            deadline=datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc),
            requirements=(RequirementSpec(VENUE, 100, False, ""),),
        ),
        now=NOW,
    )
    before = [event.event_type for event in store.read_stream(tender_id)]
    with pytest.raises(InvalidTenderError):
        publish_tender(
            store,
            tenders,
            PublishTender(organizer_id=ORGANIZER_ID, tender_id=tender_id, publish=True),
            now=NOW,
        )
    assert [event.event_type for event in store.read_stream(tender_id)] == before
    assert [item.tender_id for item in get_dashboard(tenders, ORGANIZER_ID).draft] == [tender_id]


def test_a_complete_draft_publishes():
    store, _users, tenders = _backend()
    tender_id = publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=ORGANIZER_ID,
            name="טקס פרסים",
            event_type=CULTURE,
            region="תל אביב",
            budget_amount=50_000_000,
            event_date=date(2026, 12, 1),
            deadline=datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc),
            requirements=(RequirementSpec(VENUE, 100, True, "אולם"),),
        ),
        now=NOW,
    )
    publish_tender(
        store,
        tenders,
        PublishTender(organizer_id=ORGANIZER_ID, tender_id=tender_id, publish=True),
        now=NOW,
    )
    assert store.read_stream(tender_id)[-1].event_type == TENDER_PUBLISHED
    assert [item.tender_id for item in get_dashboard(tenders, ORGANIZER_ID).open] == [tender_id]


def test_publish_form_explains_a_past_deadline_a_past_event_and_a_late_deadline(client, backend):
    _sign_in(client, ORGANIZER)
    fields = {
        "name": "טקס פרסים",
        "event_type": CULTURE,
        "region": "תל אביב",
        "budget": "500000",
        "alpha": "0.6",
        "req_type": [VENUE],
        "req_weight": ["100"],
        "req_threshold": ["1"],
        "req_description": ["אולם"],
        "intent": "publish",
    }
    past_deadline = client.post(
        "/organizer/tenders/new",
        data={**fields, "event_date": "2026-12-01", "deadline": "2020-01-01T10:00"},
    )
    past_event = client.post(
        "/organizer/tenders/new",
        data={**fields, "event_date": "2020-01-01", "deadline": "2026-11-01T18:00"},
    )
    late_deadline = client.post(
        "/organizer/tenders/new",
        data={**fields, "event_date": "2026-12-01", "deadline": "2026-12-01T10:00"},
    )
    assert "מועד ההגשה חייב להיות בעתיד." in past_deadline.get_data(as_text=True)
    assert "תאריך האירוע חייב להיות היום או מאוחר יותר." in past_event.get_data(as_text=True)
    assert "מועד ההגשה חייב להיות לפני תאריך האירוע." in late_deadline.get_data(as_text=True)
    assert 'value="טקס פרסים"' in late_deadline.get_data(as_text=True)
    store, _users, _tenders = backend
    assert store.read_all() == []


def test_an_event_dated_today_cannot_be_published():
    store, _users, tenders = _backend()
    with pytest.raises(DeadlineAfterEventError):
        publish_tender(
            store,
            tenders,
            PublishTender(
                organizer_id=ORGANIZER_ID,
                name="טקס פרסים",
                event_type=CULTURE,
                region="תל אביב",
                budget_amount=50_000_000,
                event_date=israel_today(NOW),
                deadline=NOW + timedelta(hours=1),
                requirements=(RequirementSpec(VENUE, 100, True, "אולם"),),
                publish=True,
            ),
            now=NOW,
        )
    assert store.read_all() == []
