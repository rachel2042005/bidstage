"""Tender publication: commands, projections, and role-gated pages.

Seams: PublishTender, AddRequirement, rm_tender_search / rm_tender_detail,
and the organizer and supplier tender pages.
Trace: FR-ENT-1 … FR-ENT-4, T-CMD-1, T-CMD-2, T-SC-18, T-AUTH-3, FR-SRCH-1.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.commands.add_requirement import AddRequirement, add_requirement
from app.commands.publish_tender import PublishTender, RequirementSpec, publish_tender
from app.domain.tender import (
    CONTENT,
    CULTURE,
    EXPERIENCE,
    HOST,
    MUSIC,
    OPEN,
    SPORTS,
    VENUE,
    IllegalEventOrderError,
    NoRequirementsError,
    NotTenderOwnerError,
    PastDeadlineError,
    TenderAlreadyPublishedError,
    WeightSumError,
    replay_tender,
)
from app.domain.user import ORGANIZER, SUPPLIER, UnknownEventTypeError
from app.events.event_store import StoredEvent
from app.events.event_types import REQUIREMENT_ADDED, TENDER_CREATED, TENDER_PUBLISHED
from app.events.memory import InMemoryEventStore
from app.projections.tenders import InMemoryTendersProjection, rebuild_tenders
from app.projections.users import InMemoryUsersProjection
from app.queries.get_tender import get_tender_details
from app.queries.search_tenders import search_tenders
from main import create_app

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
EVENT_DAY = date(2026, 12, 1)
DEADLINE = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)
ORGANIZER_ID = "org-1"
BUDGET = 50_000_000  # 500,000.00 ILS in agorot

WORKED_REQUIREMENTS = (
    RequirementSpec(VENUE, 25, True, "אולם ל-500 מוזמנים"),
    RequirementSpec(CONTENT, 25, False, "מופע מחווה"),
    RequirementSpec(HOST, 15, True, "מנחה מהרשימה המאושרת"),
    RequirementSpec(MUSIC, 15, False, "הרכב חי"),
    RequirementSpec(EXPERIENCE, 20, False, "חוויית קהל"),
)


def _backend():
    return InMemoryEventStore(), InMemoryUsersProjection(), InMemoryTendersProjection()


def _draft(**overrides) -> PublishTender:
    payload = dict(
        organizer_id=ORGANIZER_ID,
        name="טקס פרסים",
        event_type=CULTURE,
        event_date=EVENT_DAY,
        region="תל אביב",
        budget_amount=BUDGET,
        deadline=DEADLINE,
        alpha=Decimal("0.6"),
        requirements=(),
        publish=False,
    )
    payload.update(overrides)
    return PublishTender(**payload)


def _sign_in(client, role: str, user_id: str = ORGANIZER_ID) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = role
        sess["email"] = f"{role}@example.com"
        sess["display_name"] = "נעמה"


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


def test_publish_with_weights_100_appends_events_and_updates_search(backend):
    """T-CMD-1 / FR-ENT-3: publication writes TenderPublished and the search row."""
    store, _users, tenders = backend
    tender_id = publish_tender(
        store,
        tenders,
        _draft(requirements=WORKED_REQUIREMENTS, publish=True),
        now=NOW,
    )

    events = store.read_stream(tender_id)
    assert [event.event_type for event in events] == [
        TENDER_CREATED,
        REQUIREMENT_ADDED,
        REQUIREMENT_ADDED,
        REQUIREMENT_ADDED,
        REQUIREMENT_ADDED,
        REQUIREMENT_ADDED,
        TENDER_PUBLISHED,
    ]
    assert events[0].version == 1
    created = events[0].event_data
    assert created["estimated_budget"] == {"amount": BUDGET, "currency": "ILS"}
    assert isinstance(created["estimated_budget"]["amount"], int)
    assert created["alpha"] == "0.6"
    assert "password" not in created
    assert created["name"] == "טקס פרסים"

    found = search_tenders(tenders, status=OPEN)
    assert len(found) == 1
    assert found[0].tender_id == tender_id
    assert found[0].status == OPEN
    assert found[0].region == "תל אביב"

    details = get_tender_details(tenders, tender_id)
    assert details is not None
    assert details.weight_total == 100
    assert [item.requirement_type for item in details.requirements] == [
        VENUE,
        CONTENT,
        HOST,
        MUSIC,
        EXPERIENCE,
    ]


def test_publish_rejects_a_past_deadline_without_writing(backend):
    """T-CMD-2 / FR-ENT-4."""
    store, _users, tenders = backend
    past = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(PastDeadlineError):
        publish_tender(
            store,
            tenders,
            _draft(deadline=past, requirements=WORKED_REQUIREMENTS, publish=True),
            now=NOW,
        )
    assert store.read_all() == []
    assert search_tenders(tenders) == []


def test_publish_rejects_weights_other_than_100(backend):
    """T-SC-18 / FR-ENT-3."""
    store, _users, tenders = backend
    requirements = (RequirementSpec(VENUE, 60, False, "אולם"), RequirementSpec(CONTENT, 39, False, "תוכן"))
    with pytest.raises(WeightSumError) as caught:
        publish_tender(store, tenders, _draft(requirements=requirements, publish=True), now=NOW)
    assert caught.value.total == 99
    assert store.read_all() == []


def test_publish_rejects_zero_requirements(backend):
    """FR-ENT-4."""
    store, _users, tenders = backend
    with pytest.raises(NoRequirementsError):
        publish_tender(store, tenders, _draft(publish=True), now=NOW)
    assert store.read_all() == []


def test_draft_can_gain_requirements_until_it_publishes(backend):
    """FR-ENT-1 and FR-ENT-2: a draft exists before its requirements sum to 100."""
    store, _users, tenders = backend
    tender_id = publish_tender(
        store,
        tenders,
        _draft(requirements=(RequirementSpec(VENUE, 60, True, "אולם"),)),
        now=NOW,
    )
    assert search_tenders(tenders, organizer_id=ORGANIZER_ID)[0].status == "draft"
    assert search_tenders(tenders, status=OPEN) == []

    add_requirement(
        store,
        tenders,
        AddRequirement(
            tender_id=tender_id,
            organizer_id=ORGANIZER_ID,
            requirement_type=CONTENT,
            weight=40,
            is_threshold=False,
            description="מופע",
        ),
    )
    publish_tender(
        store,
        tenders,
        PublishTender(organizer_id=ORGANIZER_ID, tender_id=tender_id, publish=True),
        now=NOW,
    )
    state = replay_tender(store.read_stream(tender_id))
    again = replay_tender(store.read_stream(tender_id))
    assert state == again
    assert state.status == OPEN
    assert state.weight_total == 100
    assert get_tender_details(tenders, tender_id).tender.status == OPEN


def test_requirement_after_publication_is_rejected(backend):
    store, _users, tenders = backend
    tender_id = publish_tender(
        store,
        tenders,
        _draft(requirements=(RequirementSpec(VENUE, 100, False, "אולם"),), publish=True),
        now=NOW,
    )
    before = len(store.read_stream(tender_id))
    with pytest.raises(TenderAlreadyPublishedError):
        add_requirement(
            store,
            tenders,
            AddRequirement(
                tender_id=tender_id,
                organizer_id=ORGANIZER_ID,
                requirement_type=MUSIC,
                weight=10,
                is_threshold=False,
                description="להקה",
            ),
        )
    assert len(store.read_stream(tender_id)) == before


def test_another_organizer_cannot_publish_the_draft(backend):
    """T-AUTH-3: a tender is readable and publishable only by its owner."""
    store, _users, tenders = backend
    tender_id = publish_tender(
        store,
        tenders,
        _draft(requirements=(RequirementSpec(VENUE, 100, False, "אולם"),)),
        now=NOW,
    )
    with pytest.raises(NotTenderOwnerError):
        publish_tender(
            store,
            tenders,
            PublishTender(organizer_id="org-2", tender_id=tender_id, publish=True),
            now=NOW,
        )
    assert search_tenders(tenders, organizer_id="org-2") == []
    assert get_tender_details(tenders, tender_id).tender.status == "draft"


def test_tender_created_must_be_version_1():
    """T-INV-9."""
    event = StoredEvent(
        global_seq=1,
        event_id=uuid.uuid4(),
        stream_id="tender-1",
        version=2,
        event_type=TENDER_CREATED,
        event_data={},
        metadata=None,
        created_at=NOW,
    )
    with pytest.raises(IllegalEventOrderError):
        replay_tender([event])


def test_unknown_event_type_is_not_skipped():
    created = StoredEvent(
        global_seq=1,
        event_id=uuid.uuid4(),
        stream_id="tender-1",
        version=1,
        event_type=TENDER_CREATED,
        event_data={
            "name": "טקס",
            "event_type": CULTURE,
            "event_date": "2026-12-01",
            "region": "חיפה",
            "estimated_budget": {"amount": 100, "currency": "ILS"},
            "deadline": "2026-11-01T18:00:00+00:00",
            "alpha": "0.6",
            "organizer_id": ORGANIZER_ID,
        },
        metadata=None,
        created_at=NOW,
    )
    unknown = StoredEvent(
        global_seq=2,
        event_id=uuid.uuid4(),
        stream_id="tender-1",
        version=2,
        event_type="TenderRenamed",
        event_data={},
        metadata=None,
        created_at=NOW,
    )
    with pytest.raises(UnknownEventTypeError):
        replay_tender([created, unknown])


def test_rebuild_matches_the_incremental_projection(backend):
    store, _users, tenders = backend
    tender_id = publish_tender(
        store,
        tenders,
        _draft(requirements=WORKED_REQUIREMENTS, publish=True),
        now=NOW,
    )
    original = get_tender_details(tenders, tender_id)
    rebuild_tenders(store, tenders)
    rebuilt = get_tender_details(tenders, tender_id)
    assert rebuilt == original


def test_search_filters_are_conjunctive(backend):
    """FR-SRCH-1 / FR-SRCH-2: type, region and date range combine; empty filters return all open."""
    store, _users, tenders = backend
    publish_tender(
        store,
        tenders,
        _draft(
            name="קונצרט",
            event_type=CULTURE,
            region="תל אביב",
            event_date=date(2026, 12, 1),
            requirements=(RequirementSpec(MUSIC, 100, False, "תזמורת"),),
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
            event_date=date(2027, 3, 1),
            requirements=(RequirementSpec(VENUE, 100, True, "אצטדיון"),),
            publish=True,
        ),
        now=NOW,
    )
    assert len(search_tenders(tenders, status=OPEN)) == 2
    found = search_tenders(
        tenders,
        status=OPEN,
        event_type=SPORTS,
        region="חיפה",
        event_date_from=date(2027, 1, 1),
        event_date_to=date(2027, 6, 1),
    )
    assert [row.name for row in found] == ["גמר"]


def test_organizer_form_publishes_and_supplier_can_open_it(client, backend):
    _sign_in(client, ORGANIZER)
    response = client.post(
        "/organizer/tenders/new",
        data={
            "name": "טקס פרסים",
            "event_type": CULTURE,
            "event_date": "2027-06-01",
            "region": "ירושלים",
            "budget": "250000",
            "deadline": "2027-05-01T18:00",
            "alpha": "0.6",
            "req_type": [VENUE, CONTENT],
            "req_weight": ["60", "40"],
            "req_threshold": ["1", "0"],
            "req_description": ["אולם", "תוכן אומנותי"],
            "intent": "publish",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    _store, _users, tenders = backend
    published = search_tenders(tenders, status=OPEN)
    assert len(published) == 1
    assert published[0].budget_amount == 25_000_000

    _sign_in(client, SUPPLIER, user_id="sup-1")
    page = client.get("/supplier/")
    assert page.status_code == 200
    assert "טקס פרסים" in page.get_data(as_text=True)
    detail = client.get(f"/supplier/tenders/{published[0].tender_id}")
    assert detail.status_code == 200
    assert "אולם" in detail.get_data(as_text=True)


def test_supplier_cannot_open_the_create_form(client):
    _sign_in(client, SUPPLIER, user_id="sup-1")
    response = client.get("/organizer/tenders/new")
    assert response.status_code == 403
    assert "טקס".encode() not in response.data


def test_draft_is_hidden_from_suppliers_and_from_other_organizers(client, backend):
    _sign_in(client, ORGANIZER)
    response = client.post(
        "/organizer/tenders/new",
        data={
            "name": "טיוטה סגורה",
            "event_type": SPORTS,
            "event_date": "2027-08-01",
            "region": "באר שבע",
            "budget": "1000",
            "deadline": "2027-07-01T12:00",
            "alpha": "0.6",
            "req_type": [VENUE],
            "req_weight": ["40"],
            "req_threshold": ["0"],
            "req_description": ["מגרש"],
            "intent": "draft",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    tender_id = response.headers["Location"].rstrip("/").split("/")[-1]

    _sign_in(client, SUPPLIER, user_id="sup-1")
    listing = client.get("/supplier/")
    assert "טיוטה סגורה" not in listing.get_data(as_text=True)
    hidden = client.get(f"/supplier/tenders/{tender_id}")
    assert hidden.status_code == 403

    _sign_in(client, ORGANIZER, user_id="org-2")
    foreign = client.get(f"/organizer/tenders/{tender_id}")
    assert foreign.status_code == 403
    assert "מגרש".encode() not in foreign.data


def test_anonymous_create_redirects_to_login(client):
    response = client.get("/organizer/tenders/new", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
