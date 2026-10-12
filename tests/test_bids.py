"""Submit a bid. Seams confirmed for Feature 4.

Seams: submit_bid, the assessment projection, the supplier bid list,
the organizer bid count, the receipt notifier, and the supplier HTTP form.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.commands.publish_tender import RequirementSpec, publish_tender
from app.commands.submit_bid import BidResponse, SubmitBid, submit_bid
from app.domain.bid import (
    ActiveBidExistsError,
    DeadlinePassedError,
    IncompleteBidError,
    TenderNotOpenError,
)
from app.domain.tender import CONTENT, VENUE
from app.domain.user import ORGANIZER, SUPPLIER
from app.events.event_types import BID_SUBMITTED, PRICE_OFFER_SEALED
from app.events.memory import InMemoryEventStore
from app.projections.bids import CREATE_RM_BID_FOR_ASSESSMENT, InMemoryBidsProjection, rebuild_bids
from app.projections.tenders import InMemoryTendersProjection
from app.projections.users import InMemoryUsersProjection
from app.queries.get_bid import bid_count, get_bid_for_assessment, list_supplier_bids
from app.queries.get_tender import get_tender_details
from main import create_app
from tests.test_tenders import DEADLINE, _draft, _sign_in

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
SUPPLIER_ID = "sup-1"
PRICE_AGOROT = 70_000_000  # 700,000.00 ILS


class RecordingNotifier:
    def __init__(self) -> None:
        self.receipts = []

    def bid_received(self, receipt) -> None:
        self.receipts.append(receipt)


def _backend():
    return (
        InMemoryEventStore(),
        InMemoryUsersProjection(),
        InMemoryTendersProjection(),
        InMemoryBidsProjection(),
    )


def _open_tender(store, tenders) -> tuple[str, tuple]:
    tender_id = publish_tender(
        store,
        tenders,
        _draft(
            requirements=(
                RequirementSpec(VENUE, 60, True, "אולם ל-500"),
                RequirementSpec(CONTENT, 40, False, "מופע מחווה"),
            ),
            publish=True,
        ),
        now=NOW,
    )
    details = get_tender_details(tenders, tender_id)
    assert details is not None
    return tender_id, details.requirements


def test_submit_bid_seals_the_price_and_leaves_it_off_the_read_models():
    """T-CMD-4 / FR-ENT-5 / FR-ENT-6 / FR-NOT-1."""
    store, _users, tenders, bids = _backend()
    tender_id, requirements = _open_tender(store, tenders)
    notifier = RecordingNotifier()
    bid_id = submit_bid(
        store,
        bids,
        SubmitBid(
            tender_id=tender_id,
            supplier_id=SUPPLIER_ID,
            supplier_email="supplier@example.com",
            concept="מופע מחווה חזק",
            responses=tuple(
                BidResponse(item.requirement_id, f"מענה ל-{item.requirement_type}")
                for item in requirements
            ),
            price_amount=PRICE_AGOROT,
        ),
        now=NOW,
        notifier=notifier,
    )

    events = store.read_stream(bid_id)
    assert [event.event_type for event in events] == [BID_SUBMITTED, PRICE_OFFER_SEALED]
    assert events[0].version == 1
    assert events[1].version == 2
    submitted = events[0].event_data
    assert submitted["tender_id"] == tender_id
    assert submitted["supplier_id"] == SUPPLIER_ID
    assert submitted["concept"] == "מופע מחווה חזק"
    assert "price_amount" not in submitted
    assert "price" not in submitted
    sealed = events[1].event_data
    assert sealed == {
        "price_amount": PRICE_AGOROT,
        "currency": "ILS",
        "sealed_at": NOW.isoformat(),
    }
    assert isinstance(sealed["price_amount"], int)

    assessment = get_bid_for_assessment(bids, bid_id)
    assert assessment is not None
    assert assessment.concept == "מופע מחווה חזק"
    assert [item.answer for item in assessment.responses] == [
        "מענה ל-VENUE",
        "מענה ל-CONTENT",
    ]
    assert "price_amount" not in assessment.__dict__

    mine = list_supplier_bids(bids, SUPPLIER_ID)
    assert len(mine) == 1
    assert mine[0].bid_id == bid_id
    assert mine[0].tender_id == tender_id
    assert mine[0].tender_name == "טקס פרסים"
    assert mine[0].status == "submitted"
    assert mine[0].submitted_at == NOW
    assert "price_amount" not in mine[0].__dict__

    assert bid_count(bids, tender_id) == 1
    assert len(notifier.receipts) == 1
    receipt = notifier.receipts[0]
    assert receipt.to_email == "supplier@example.com"
    assert receipt.tender_name == "טקס פרסים"
    assert receipt.bid_id == bid_id
    assert "price_amount" not in receipt.__dict__
    message = f"{receipt.subject}\n{receipt.body}"
    assert "טקס פרסים" in message
    assert bid_id in message
    assert "700000" not in message
    assert "70,000,000" not in message


class RaisingNotifier:
    def bid_received(self, receipt) -> None:
        raise RuntimeError("gmail down")


def _command(tender_id, requirements, **overrides) -> SubmitBid:
    payload = dict(
        tender_id=tender_id,
        supplier_id=SUPPLIER_ID,
        supplier_email="supplier@example.com",
        concept="מופע מחווה חזק",
        responses=tuple(
            BidResponse(item.requirement_id, f"מענה ל-{item.requirement_type}")
            for item in requirements
        ),
        price_amount=PRICE_AGOROT,
    )
    payload.update(overrides)
    return SubmitBid(**payload)


def test_submit_bid_rejects_a_missing_response_without_writing_or_emailing():
    """T-CMD-3 / FR-ENT-5."""
    store, _users, tenders, bids = _backend()
    tender_id, requirements = _open_tender(store, tenders)
    notifier = RecordingNotifier()
    partial = tuple(
        BidResponse(item.requirement_id, "מענה") for item in requirements[:1]
    )
    with pytest.raises(IncompleteBidError):
        submit_bid(
            store,
            bids,
            _command(tender_id, requirements, responses=partial),
            now=NOW,
            notifier=notifier,
        )
    assert [event.event_type for event in store.read_all() if event.event_type == BID_SUBMITTED] == []
    assert notifier.receipts == []
    assert bid_count(bids, tender_id) == 0


def test_submit_bid_rejects_the_deadline_instant():
    """T-CMD-5 / FR-ENT-8. A submit at the deadline is already too late."""
    store, _users, tenders, bids = _backend()
    tender_id, requirements = _open_tender(store, tenders)
    notifier = RecordingNotifier()
    with pytest.raises(DeadlinePassedError):
        submit_bid(
            store,
            bids,
            _command(tender_id, requirements),
            now=DEADLINE,
            notifier=notifier,
        )
    assert notifier.receipts == []
    assert bid_count(bids, tender_id) == 0


def test_second_active_bid_is_rejected():
    """T-CMD-6 / FR-ENT-8."""
    store, _users, tenders, bids = _backend()
    tender_id, requirements = _open_tender(store, tenders)
    submit_bid(store, bids, _command(tender_id, requirements), now=NOW)
    with pytest.raises(ActiveBidExistsError):
        submit_bid(
            store,
            bids,
            _command(tender_id, requirements, supplier_email="supplier@example.com"),
            now=NOW,
        )
    assert bid_count(bids, tender_id) == 1
    assert len(list_supplier_bids(bids, SUPPLIER_ID)) == 1


def test_draft_tender_rejects_a_bid():
    store, _users, tenders, bids = _backend()
    tender_id = publish_tender(
        store,
        tenders,
        _draft(requirements=(RequirementSpec(VENUE, 40, False, "אולם"),)),
        now=NOW,
    )
    details = get_tender_details(tenders, tender_id)
    assert details is not None
    with pytest.raises(TenderNotOpenError):
        submit_bid(store, bids, _command(tender_id, details.requirements), now=NOW)
    assert bid_count(bids, tender_id) == 0


def test_gmail_failure_still_keeps_the_bid():
    """T-CMD-9 / FR-NOT-4."""
    store, _users, tenders, bids = _backend()
    tender_id, requirements = _open_tender(store, tenders)
    bid_id = submit_bid(
        store,
        bids,
        _command(tender_id, requirements),
        now=NOW,
        notifier=RaisingNotifier(),
    )
    events = store.read_stream(bid_id)
    assert [event.event_type for event in events] == [BID_SUBMITTED, PRICE_OFFER_SEALED]
    assert get_bid_for_assessment(bids, bid_id) is not None


def test_rebuild_restores_bids_without_the_price():
    store, _users, tenders, bids = _backend()
    tender_id, requirements = _open_tender(store, tenders)
    bid_id = submit_bid(store, bids, _command(tender_id, requirements), now=NOW)
    rebuild_bids(store, bids)
    assessment = get_bid_for_assessment(bids, bid_id)
    assert assessment is not None
    assert assessment.concept == "מופע מחווה חזק"
    assert "price_amount" not in assessment.__dict__
    assert bid_count(bids, tender_id) == 1
    mine = list_supplier_bids(bids, SUPPLIER_ID)
    assert mine[0].submitted_at == NOW
    assert mine[0].tender_name == "טקס פרסים"


def test_assessment_table_has_no_price_column():
    folded = CREATE_RM_BID_FOR_ASSESSMENT.lower()
    assert "price" not in folded


@pytest.fixture
def backend():
    return _backend()


@pytest.fixture
def client(backend):
    store, users, tenders, bids = backend
    application = create_app(
        event_store=store,
        users=users,
        tenders=tenders,
        bids=bids,
        bid_notifier=RecordingNotifier(),
    )
    application.config["TESTING"] = True
    application.config["SECRET_KEY"] = "test-secret"
    return application.test_client()


def test_supplier_submits_from_the_form_and_organizer_sees_only_the_count(client, backend):
    store, _users, tenders, bids = backend
    tender_id, requirements = _open_tender(store, tenders)
    _sign_in(client, SUPPLIER, user_id=SUPPLIER_ID)
    page = client.get(f"/supplier/tenders/{tender_id}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "הגשת הצעה" in html
    assert "אולם ל-500" in html

    form = {
        "concept": "מופע מחווה חזק",
        "price": "700000",
    }
    for item in requirements:
        form[f"answer_{item.requirement_id}"] = f"מענה ל-{item.requirement_type}"
    submitted = client.post(
        f"/supplier/tenders/{tender_id}/bids",
        data=form,
        follow_redirects=True,
    )
    body = submitted.get_data(as_text=True)
    assert submitted.status_code == 200
    assert "ההצעה התקבלה" in body
    assert "טקס פרסים" in body
    assert "הוגשה" in body
    assert "700000" not in body
    assert "70,000,000" not in body

    assessment = get_bid_for_assessment(bids, list_supplier_bids(bids, SUPPLIER_ID)[0].bid_id)
    assert assessment is not None
    assert "price_amount" not in assessment.__dict__
    sealed = [
        event for event in store.read_all() if event.event_type == PRICE_OFFER_SEALED
    ]
    assert sealed[0].event_data["price_amount"] == PRICE_AGOROT

    _sign_in(client, ORGANIZER)
    organizer = client.get(f"/organizer/tenders/{tender_id}")
    organizer_html = organizer.get_data(as_text=True)
    assert 'id="bid-count">1<' in organizer_html
    assert "מופע מחווה חזק" not in organizer_html
    listing = client.get("/organizer/")
    assert "1 הצעות" in listing.get_data(as_text=True) or ">1</span> הצעות" in listing.get_data(as_text=True)

    forbidden = client.post(f"/supplier/tenders/{tender_id}/bids", data=form)
    assert forbidden.status_code == 403
    assert "הגשת הצעה" not in forbidden.get_data(as_text=True)

    client.get("/logout")
    anonymous = client.post(f"/supplier/tenders/{tender_id}/bids", data=form)
    assert anonymous.status_code == 302
    assert "/login" in anonymous.headers["Location"]
