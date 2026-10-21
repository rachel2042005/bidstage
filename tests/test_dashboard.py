"""Dashboard, close, and comparison. Seams confirmed for Feature 5.

Seams: close_tender, get_dashboard, get_bid_comparison, reveal_prices,
and the organizer HTTP pages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest

from app.commands.close_tender import CloseTender, close_tender
from app.commands.publish_tender import RequirementSpec, publish_tender
from app.commands.submit_bid import BidResponse, SubmitBid, submit_bid
from app.domain.tender import NotTenderOwnerError, TenderNotClosableError, VENUE
from app.events.event_types import TENDER_CLOSED_FOR_BIDS, TENDER_FAILED
from app.events.memory import InMemoryEventStore
from app.projections.bids import InMemoryBidsProjection
from app.projections.tenders import InMemoryTendersProjection
from app.projections.users import InMemoryUsersProjection
from app.queries.get_dashboard import get_dashboard
from tests.test_tenders import DEADLINE, NOW as PUBLISHED_AT, ORGANIZER_ID, _draft

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def _backend():
    return (
        InMemoryEventStore(),
        InMemoryUsersProjection(),
        InMemoryTendersProjection(),
        InMemoryBidsProjection(),
    )


def _bid(store, tenders, bids, tender_id: str, supplier_id: str = "sup-1") -> str:
    from app.queries.get_tender import get_tender_details

    details = get_tender_details(tenders, tender_id)
    assert details is not None
    return submit_bid(
        store,
        bids,
        SubmitBid(
            tender_id=tender_id,
            supplier_id=supplier_id,
            supplier_email=f"{supplier_id}@example.com",
            supplier_display_name=supplier_id,
            concept="קונספט",
            responses=tuple(
                BidResponse(item.requirement_id, "מענה") for item in details.requirements
            ),
            price_amount=70_000_000,
        ),
        now=PUBLISHED_AT,
        tenders=tenders,
    )


def _open(store, tenders) -> str:
    return publish_tender(
        store,
        tenders,
        _draft(
            requirements=(RequirementSpec(VENUE, 100, True, "אולם"),),
            publish=True,
        ),
        now=PUBLISHED_AT,
    )


def test_close_after_the_deadline_moves_the_tender_to_under_evaluation():
    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    _bid(store, tenders, bids, tender_id)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=False),
        now=NOW,
    )
    events = store.read_stream(tender_id)
    assert events[-1].event_type == TENDER_CLOSED_FOR_BIDS
    assert events[-1].event_data["bid_count"] == 1
    dashboard = get_dashboard(tenders, ORGANIZER_ID)
    assert [item.tender_id for item in dashboard.under_evaluation] == [tender_id]
    assert dashboard.under_evaluation[0].bid_count == 1
    assert dashboard.open == ()


def test_zero_bids_on_close_fails_the_tender():
    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=PUBLISHED_AT,
    )
    types = [event.event_type for event in store.read_stream(tender_id)]
    assert types[-2:] == [TENDER_CLOSED_FOR_BIDS, TENDER_FAILED]
    dashboard = get_dashboard(tenders, ORGANIZER_ID)
    assert [item.tender_id for item in dashboard.failed] == [tender_id]
    assert dashboard.under_evaluation == ()


def test_close_rejects_a_draft_another_organizer_and_a_second_close():
    store, _users, tenders, bids = _backend()
    draft_id = publish_tender(store, tenders, _draft(), now=PUBLISHED_AT)
    before = [event.event_type for event in store.read_stream(draft_id)]
    with pytest.raises(TenderNotClosableError):
        close_tender(
            store,
            tenders,
            bids,
            CloseTender(tender_id=draft_id, organizer_id=ORGANIZER_ID, explicit=True),
            now=NOW,
        )
    assert [event.event_type for event in store.read_stream(draft_id)] == before

    tender_id = _open(store, tenders)
    with pytest.raises(NotTenderOwnerError):
        close_tender(
            store,
            tenders,
            bids,
            CloseTender(tender_id=tender_id, organizer_id="org-2", explicit=True),
            now=NOW,
        )
    assert TENDER_CLOSED_FOR_BIDS not in [
        event.event_type for event in store.read_stream(tender_id)
    ]


def test_comparison_stays_sealed_until_reveal_and_then_ranks_the_worked_example():
    """FR-TBL-4 and the §7 worked example: B finishes ahead of A."""
    from decimal import Decimal

    from app.commands.reveal_prices import RevealPrices, reveal_prices
    from app.domain.tender import BidsNotAssessedError
    from app.events.event_types import PRICES_REVEALED, QUALITY_ASSESSMENT_COMPLETED
    from app.queries.get_bid_comparison import get_bid_comparison

    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    bid_a = _offer(store, tenders, bids, tender_id, "sup-a", "ספק א", 90_000_000, PUBLISHED_AT)
    bid_b = _offer(
        store,
        tenders,
        bids,
        tender_id,
        "sup-b",
        "ספק ב",
        70_000_000,
        PUBLISHED_AT.replace(minute=5),
    )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=PUBLISHED_AT,
    )
    sealed = get_bid_comparison(bids, tender_id)
    assert [row.supplier_name for row in sealed] == ["ספק א", "ספק ב"]
    assert all(row.price_amount is None and row.quality is None and row.final_score is None for row in sealed)

    with pytest.raises(BidsNotAssessedError):
        reveal_prices(
            store,
            tenders,
            bids,
            RevealPrices(tender_id=tender_id, organizer_id=ORGANIZER_ID),
            now=NOW,
        )
    assert PRICES_REVEALED not in [event.event_type for event in store.read_stream(tender_id)]

    _append(store, bid_a, QUALITY_ASSESSMENT_COMPLETED, {"quality_score": "82", "requirement_scores": []})
    _append(store, bid_b, QUALITY_ASSESSMENT_COMPLETED, {"quality_score": "74", "requirement_scores": []})
    reveal_prices(
        store,
        tenders,
        bids,
        RevealPrices(tender_id=tender_id, organizer_id=ORGANIZER_ID),
        now=NOW,
    )
    ranked = get_bid_comparison(bids, tender_id, sort="final")
    assert [(row.supplier_name, row.rank, row.final_score, row.price_score) for row in ranked] == [
        ("ספק ב", 1, Decimal("84.4"), Decimal("100.0")),
        ("ספק א", 2, Decimal("80.3"), Decimal("77.8")),
    ]


def test_a_disqualified_price_does_not_change_the_qualified_scores():
    from decimal import Decimal

    from app.commands.reveal_prices import RevealPrices, reveal_prices
    from app.domain.bid import DISQUALIFIED
    from app.events.event_types import BID_DISQUALIFIED, QUALITY_ASSESSMENT_COMPLETED
    from app.queries.get_bid_comparison import get_bid_comparison

    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    bid_a = _offer(store, tenders, bids, tender_id, "sup-a", "ספק א", 90_000_000, PUBLISHED_AT)
    bid_b = _offer(
        store, tenders, bids, tender_id, "sup-b", "ספק ב", 70_000_000, PUBLISHED_AT.replace(minute=5)
    )
    bid_c = _offer(
        store, tenders, bids, tender_id, "sup-c", "ספק ג", 50_000_000, PUBLISHED_AT.replace(minute=10)
    )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=PUBLISHED_AT,
    )
    _append(store, bid_a, QUALITY_ASSESSMENT_COMPLETED, {"quality_score": "82", "requirement_scores": []})
    _append(store, bid_b, QUALITY_ASSESSMENT_COMPLETED, {"quality_score": "74", "requirement_scores": []})
    _append(store, bid_c, BID_DISQUALIFIED, {"reason": "threshold", "failed_requirement_id": "venue"})
    reveal_prices(
        store,
        tenders,
        bids,
        RevealPrices(tender_id=tender_id, organizer_id=ORGANIZER_ID),
        now=NOW,
    )
    ranked = get_bid_comparison(bids, tender_id, sort="final")
    assert [(row.supplier_name, row.rank, row.final_score, row.price_score) for row in ranked[:2]] == [
        ("ספק ב", 1, Decimal("84.4"), Decimal("100.0")),
        ("ספק א", 2, Decimal("80.3"), Decimal("77.8")),
    ]
    disqualified = ranked[-1]
    assert disqualified.supplier_name == "ספק ג"
    assert disqualified.rank is None
    assert disqualified.status == DISQUALIFIED
    assert disqualified.price_amount == 50_000_000


def test_reveal_fails_the_tender_when_every_bid_is_disqualified():
    from app.commands.reveal_prices import RevealPrices, reveal_prices
    from app.events.event_types import BID_DISQUALIFIED, PRICES_REVEALED
    from app.queries.get_bid_comparison import get_bid_comparison

    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    bid_id = _offer(store, tenders, bids, tender_id, "sup-a", "ספק א", 50_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=PUBLISHED_AT,
    )
    _append(store, bid_id, BID_DISQUALIFIED, {"reason": "threshold", "failed_requirement_id": "venue"})
    reveal_prices(
        store,
        tenders,
        bids,
        RevealPrices(tender_id=tender_id, organizer_id=ORGANIZER_ID),
        now=NOW,
    )
    types = [event.event_type for event in store.read_stream(tender_id)]
    assert types[-1] == TENDER_FAILED
    assert PRICES_REVEALED not in types
    assert get_bid_comparison(bids, tender_id)[0].price_amount is None
    assert get_dashboard(tenders, ORGANIZER_ID).failed[0].tender_id == tender_id


def _offer(store, tenders, bids, tender_id, supplier_id, name, price, when) -> str:
    from app.queries.get_tender import get_tender_details

    details = get_tender_details(tenders, tender_id)
    assert details is not None
    return submit_bid(
        store,
        bids,
        SubmitBid(
            tender_id=tender_id,
            supplier_id=supplier_id,
            supplier_email=f"{supplier_id}@example.com",
            supplier_display_name=name,
            concept="קונספט",
            responses=tuple(
                BidResponse(item.requirement_id, "מענה") for item in details.requirements
            ),
            price_amount=price,
        ),
        now=when,
        tenders=tenders,
    )


def _append(store, stream_id: str, event_type: str, data: dict) -> None:
    store.append(stream_id, store.last_version(stream_id), event_type, data)


@pytest.fixture
def backend():
    return _backend()


@pytest.fixture
def client(backend):
    from main import create_app

    store, users, tenders, bids = backend
    application = create_app(event_store=store, users=users, tenders=tenders, bids=bids)
    application.config["TESTING"] = True
    application.config["SECRET_KEY"] = "test-secret"
    return application.test_client()


def test_dashboard_and_comparison_pages_hide_prices_until_reveal(client, backend):
    from app.domain.user import ORGANIZER, SUPPLIER
    from app.events.event_types import QUALITY_ASSESSMENT_COMPLETED
    from tests.test_tenders import _sign_in

    store, _users, tenders, bids = backend
    tender_id = _open(store, tenders)
    bid_a = _offer(store, tenders, bids, tender_id, "sup-a", "ספק א", 90_000_000, PUBLISHED_AT)
    bid_b = _offer(
        store, tenders, bids, tender_id, "sup-b", "ספק ב", 70_000_000, PUBLISHED_AT.replace(minute=5)
    )
    _sign_in(client, ORGANIZER)
    home = client.get("/organizer/").get_data(as_text=True)
    for anchor in (
        "group-draft",
        "group-open",
        "group-under-evaluation",
        "group-decided",
        "group-failed",
        "group-cancelled",
    ):
        assert f'id="{anchor}"' in home
    assert home.count("אין מכרזים") == 5
    assert 'data-deadline="' in home
    assert "2026-11-01" in home
    assert ">2</span> הצעות" in home

    closed = client.post(f"/organizer/tenders/{tender_id}/close", follow_redirects=True)
    sealed = closed.get_data(as_text=True)
    assert closed.status_code == 200
    assert "חסום" in sealed
    assert "ספק א" in sealed
    assert "ספק ב" in sealed
    assert "900,000" not in sealed
    assert "700,000" not in sealed

    _sign_in(client, SUPPLIER, user_id="sup-a")
    forbidden = client.get(f"/organizer/tenders/{tender_id}/comparison")
    forbidden_body = forbidden.get_data(as_text=True)
    assert forbidden.status_code == 403
    assert "ספק א" not in forbidden_body
    assert "900,000" not in forbidden_body

    _sign_in(client, ORGANIZER, user_id="org-2")
    foreign = client.get(f"/organizer/tenders/{tender_id}/comparison")
    foreign_body = foreign.get_data(as_text=True)
    assert foreign.status_code == 403
    assert "ספק א" not in foreign_body

    client.get("/logout")
    anonymous = client.get(f"/organizer/tenders/{tender_id}/comparison", follow_redirects=False)
    assert anonymous.status_code == 302
    assert "/login" in anonymous.headers["Location"]

    _append(store, bid_a, QUALITY_ASSESSMENT_COMPLETED, {"quality_score": "82", "requirement_scores": []})
    _append(store, bid_b, QUALITY_ASSESSMENT_COMPLETED, {"quality_score": "74", "requirement_scores": []})
    _sign_in(client, ORGANIZER)
    revealed = client.post(
        f"/organizer/tenders/{tender_id}/reveal",
        follow_redirects=True,
    )
    ordered = client.get(f"/organizer/tenders/{tender_id}/comparison?sort=final")
    body = ordered.get_data(as_text=True)
    assert revealed.status_code == 200
    assert body.index("ספק ב") < body.index("ספק א")
    assert "84.4" in body
    assert "80.3" in body


def test_dashboard_chart_payload_matches_the_groups_and_hides_other_organizers(client, backend):
    import json

    from app.domain.user import ORGANIZER, SUPPLIER
    from tests.test_tenders import _sign_in

    store, _users, tenders, bids = backend
    tender_id = _open(store, tenders)
    _offer(store, tenders, bids, tender_id, "sup-a", "ספק א", 90_000_000, PUBLISHED_AT)
    _offer(
        store, tenders, bids, tender_id, "sup-b", "ספק ב", 70_000_000, PUBLISHED_AT.replace(minute=5)
    )
    _sign_in(client, ORGANIZER)
    home = client.get("/organizer/")
    body = home.get_data(as_text=True)
    assert home.status_code == 200
    payload = json.loads(_script_text(body, "dashboard-charts"))
    assert payload["statuses"] == [
        {"key": "draft", "label": "טיוטה", "count": 0, "anchor": "group-draft"},
        {"key": "open", "label": "פתוח להצעות", "count": 1, "anchor": "group-open"},
        {"key": "under_evaluation", "label": "בהערכה", "count": 0, "anchor": "group-under-evaluation"},
        {"key": "decided", "label": "הוכרע", "count": 0, "anchor": "group-decided"},
        {"key": "failed", "label": "נכשל", "count": 0, "anchor": "group-failed"},
        {"key": "cancelled", "label": "בוטל", "count": 0, "anchor": "group-cancelled"},
    ]
    assert payload["bids"] == [
        {
            "name": "טקס פרסים",
            "bid_count": 2,
            "url": f"/organizer/tenders/{tender_id}",
        }
    ]
    assert "900,000" not in body
    assert "700,000" not in body
    assert "chart.js" in body
    for anchor in (
        "group-draft",
        "group-open",
        "group-under-evaluation",
        "group-decided",
        "group-failed",
        "group-cancelled",
    ):
        assert f'id="{anchor}"' in body
    assert body.count("אין מכרזים") == 5
    assert 'data-deadline="' in body
    assert ">2</span> הצעות" in body

    _sign_in(client, SUPPLIER, user_id="sup-a")
    forbidden = client.get("/organizer/")
    forbidden_body = forbidden.get_data(as_text=True)
    assert forbidden.status_code == 403
    assert "dashboard-charts" not in forbidden_body
    assert "טקס פרסים" not in forbidden_body

    _sign_in(client, ORGANIZER, user_id="org-2")
    foreign = client.get("/organizer/").get_data(as_text=True)
    foreign_payload = json.loads(_script_text(foreign, "dashboard-charts"))
    assert foreign_payload["bids"] == []
    assert "טקס פרסים" not in foreign
    assert all(item["count"] == 0 for item in foreign_payload["statuses"])

    client.get("/logout")
    anonymous = client.get("/organizer/", follow_redirects=False)
    assert anonymous.status_code == 302
    assert "/login" in anonymous.headers["Location"]


class _ScriptGrabber(HTMLParser):
    def __init__(self, script_id: str) -> None:
        super().__init__()
        self.script_id = script_id
        self._capture = False
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == self.script_id:
            self._capture = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._capture = False

    def handle_data(self, data):
        if self._capture:
            self.text.append(data)


def _script_text(html: str, script_id: str) -> str:
    grabber = _ScriptGrabber(script_id)
    grabber.feed(html)
    return "".join(grabber.text)

