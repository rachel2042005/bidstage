"""Organizer semantic search over bids. FR-SRCH-4, FR-SRCH-6, T-AUTH-4.

Seams: search_organizer_bids, and the organizer search page.
The bid search port is a stand-in. No test opens Chroma or OpenAI.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.commands.close_tender import CloseTender, close_tender
from app.commands.publish_tender import PublishTender, RequirementSpec, publish_tender
from app.domain.tender import CULTURE, VENUE, NotTenderOwnerError, TenderNotFoundError
from app.queries.search_bids import search_organizer_bids
from tests.test_dashboard import NOW, PUBLISHED_AT, _backend, _offer, _open
from tests.test_tenders import BUDGET, DEADLINE, EVENT_DAY, ORGANIZER_ID


class _Hit:
    def __init__(self, bid_id: str, tender_id: str, excerpt: str) -> None:
        self.bid_id = bid_id
        self.tender_id = tender_id
        self.excerpt = excerpt


class RecordingSearch:
    def __init__(self, hits: list[_Hit]) -> None:
        self._hits = hits
        self.calls: list[tuple] = []

    def search_bids(self, query: str, tender_ids: list[str], top_k: int = 5) -> list[_Hit]:
        self.calls.append((query, list(tender_ids), top_k))
        return list(self._hits)


def test_search_returns_named_hits_in_port_order_and_drops_foreign_bids():
    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    names = ("ספק א", "ספק ב", "ספק ג", "ספק ד", "ספק ה", "ספק ו")
    bid_ids = [
        _offer(
            store,
            tenders,
            bids,
            tender_id,
            f"sup-{index}",
            name,
            70_000_000,
            PUBLISHED_AT.replace(minute=index),
        )
        for index, name in enumerate(names)
    ]
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    foreign_id = _open_for(store, tenders, "org-2", "מכרז זר")
    foreign_bid = _offer(
        store, tenders, bids, foreign_id, "sup-z", "ספק ז", 50_000_000, PUBLISHED_AT
    )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=foreign_id, organizer_id="org-2", explicit=True),
        now=NOW,
    )
    ordered = [
        _Hit(bid_ids[1], tender_id, "קטע מחווה"),
        _Hit(bid_ids[1], tender_id, "אותו קטע שוב"),
        _Hit(foreign_bid, foreign_id, "הצעה של מארגן אחר"),
        _Hit("missing-bid", tender_id, "אין שורה להצעה"),
        _Hit(bid_ids[0], tender_id, "קונספט ראשון"),
        _Hit(bid_ids[2], tender_id, "קונספט שלישי"),
        _Hit(bid_ids[3], tender_id, "קונספט רביעי"),
        _Hit(bid_ids[4], tender_id, "קונספט חמישי"),
        _Hit(bid_ids[5], tender_id, "קונספט שישי"),
    ]
    searcher = RecordingSearch(ordered)
    result = search_organizer_bids(
        tenders,
        bids,
        searcher,
        organizer_id=ORGANIZER_ID,
        query="מופע מחווה",
    )
    assert searcher.calls == [("מופע מחווה", [tender_id], 5)]
    assert [(hit.supplier_name, hit.tender_name, hit.excerpt) for hit in result.hits] == [
        ("ספק ב", "טקס פרסים", "קטע מחווה"),
        ("ספק א", "טקס פרסים", "קונספט ראשון"),
        ("ספק ג", "טקס פרסים", "קונספט שלישי"),
        ("ספק ד", "טקס פרסים", "קונספט רביעי"),
        ("ספק ה", "טקס פרסים", "קונספט חמישי"),
    ]
    assert result.unavailable is False
    assert "ספק ז" not in [hit.supplier_name for hit in result.hits]
    assert "ספק ו" not in [hit.supplier_name for hit in result.hits]


def test_blank_query_does_not_call_the_port():
    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    searcher = RecordingSearch([_Hit("bid", tender_id, "קטע")])
    result = search_organizer_bids(
        tenders,
        bids,
        searcher,
        organizer_id=ORGANIZER_ID,
        query="   ",
    )
    assert searcher.calls == []
    assert result.hits == ()
    assert result.unavailable is False


def test_open_tenders_are_left_out_of_an_unscoped_search():
    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    bid_id = _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    searcher = RecordingSearch([_Hit(bid_id, tender_id, "קטע פתוח")])
    result = search_organizer_bids(
        tenders,
        bids,
        searcher,
        organizer_id=ORGANIZER_ID,
        query="מחווה",
    )
    assert searcher.calls == []
    assert result.hits == ()


def test_a_named_tender_outside_search_is_refused_before_the_port_is_called():
    store, _users, tenders, bids = _backend()
    open_id = _open(store, tenders)
    draft_id = publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=ORGANIZER_ID,
            name="טיוטה",
            event_type=CULTURE,
            event_date=EVENT_DAY,
            region="תל אביב",
            budget_amount=BUDGET,
            deadline=DEADLINE,
            alpha=Decimal("0.6"),
            requirements=(),
            publish=False,
        ),
        now=PUBLISHED_AT,
    )
    foreign_id = _open_for(store, tenders, "org-2", "מכרז זר")
    searcher = RecordingSearch([])
    with pytest.raises(TenderNotFoundError):
        search_organizer_bids(
            tenders,
            bids,
            searcher,
            organizer_id=ORGANIZER_ID,
            query="מחווה",
            tender_id="missing-tender",
        )
    for refused_id in (open_id, draft_id, foreign_id):
        with pytest.raises(NotTenderOwnerError):
            search_organizer_bids(
                tenders,
                bids,
                searcher,
                organizer_id=ORGANIZER_ID,
                query="מחווה",
                tender_id=refused_id,
            )
    assert searcher.calls == []


def test_a_selected_tender_is_the_only_id_passed_to_the_port():
    store, _users, tenders, bids = _backend()
    first = _open(store, tenders)
    second = _open_for(store, tenders, ORGANIZER_ID, "טקס שני")
    bid_id = _offer(store, tenders, bids, second, "sup-2", "ספק ב", 70_000_000, PUBLISHED_AT)
    for tender_id, owner in ((first, ORGANIZER_ID), (second, ORGANIZER_ID)):
        close_tender(
            store,
            tenders,
            bids,
            CloseTender(tender_id=tender_id, organizer_id=owner, explicit=True),
            now=NOW,
        )
    searcher = RecordingSearch([_Hit(bid_id, second, "קטע שני")])
    result = search_organizer_bids(
        tenders,
        bids,
        searcher,
        organizer_id=ORGANIZER_ID,
        query="מחווה",
        tender_id=second,
    )
    assert searcher.calls == [("מחווה", [second], 5)]
    assert [(hit.supplier_name, hit.tender_name, hit.excerpt) for hit in result.hits] == [
        ("ספק ב", "טקס שני", "קטע שני"),
    ]


def test_a_failed_tender_stays_in_the_search_scope():
    store, _users, tenders, bids = _backend()
    empty_id = _open_for(store, tenders, ORGANIZER_ID, "מכרז ריק")
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=empty_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    tender_id = _open(store, tenders)
    bid_id = _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    searcher = RecordingSearch([_Hit(bid_id, tender_id, "קטע")])
    search_organizer_bids(
        tenders,
        bids,
        searcher,
        organizer_id=ORGANIZER_ID,
        query="מחווה",
    )
    assert searcher.calls == [("מחווה", [tender_id, empty_id], 5)]


def test_a_raising_port_marks_the_search_unavailable():
    store, _users, tenders, bids = _backend()
    tender_id = _open(store, tenders)
    _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )

    class RaisingSearch:
        def search_bids(self, query: str, tender_ids: list[str], top_k: int = 5):
            raise RuntimeError("chroma down")

    result = search_organizer_bids(
        tenders,
        bids,
        RaisingSearch(),
        organizer_id=ORGANIZER_ID,
        query="מחווה",
    )
    assert result.unavailable is True
    assert result.hits == ()


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
    application.extensions["bid_search"] = RecordingSearch([])
    return application.test_client()


def test_organizer_search_page_shows_the_excerpt_and_hides_the_price(client, backend):
    from app.domain.user import ORGANIZER
    from tests.test_tenders import _sign_in

    store, _users, tenders, bids = backend
    open_id = _open_for(store, tenders, ORGANIZER_ID, "עדיין פתוח")
    tender_id = _open(store, tenders)
    bid_id = _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    searcher = RecordingSearch([_Hit(bid_id, tender_id, "קטע מחווה")])
    client.application.extensions["bid_search"] = searcher
    _sign_in(client, ORGANIZER)
    page = client.get(f"/organizer/search?q=מחווה&tender_id={tender_id}")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "ספק א" in body
    assert "טקס פרסים" in body
    assert "קטע מחווה" in body
    assert f"/organizer/tenders/{tender_id}/comparison" in body
    assert "700,000" not in body
    assert "עדיין פתוח" not in body
    assert searcher.calls == [("מחווה", [tender_id], 5)]
    detail = client.get(f"/organizer/tenders/{tender_id}").get_data(as_text=True)
    assert f"/organizer/search?tender_id={tender_id}" in detail
    open_page = client.get(f"/organizer/tenders/{open_id}").get_data(as_text=True)
    assert f"/organizer/search?tender_id={open_id}" not in open_page


def test_search_page_refuses_other_readers_and_tenders_that_are_not_searchable(client, backend):
    from app.domain.user import ORGANIZER, SUPPLIER
    from tests.test_tenders import _sign_in

    store, _users, tenders, bids = backend
    tender_id = _open(store, tenders)
    bid_id = _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    open_id = _open_for(store, tenders, ORGANIZER_ID, "עדיין פתוח")
    foreign_id = _open_for(store, tenders, "org-2", "מכרז זר")
    foreign_bid = _offer(
        store, tenders, bids, foreign_id, "sup-z", "ספק ז", 50_000_000, PUBLISHED_AT
    )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=foreign_id, organizer_id="org-2", explicit=True),
        now=NOW,
    )
    searcher = RecordingSearch(
        [
            _Hit(bid_id, tender_id, "קטע מחווה"),
            _Hit(foreign_bid, foreign_id, "הצעה זרה"),
        ]
    )
    client.application.extensions["bid_search"] = searcher

    _sign_in(client, SUPPLIER, user_id="sup-1")
    forbidden = client.get("/organizer/search?q=מחווה")
    forbidden_body = forbidden.get_data(as_text=True)
    assert forbidden.status_code == 403
    assert "ספק א" not in forbidden_body
    assert "קטע מחווה" not in forbidden_body

    client.get("/logout")
    anonymous = client.get("/organizer/search?q=מחווה", follow_redirects=False)
    assert anonymous.status_code == 302
    assert "/login" in anonymous.headers["Location"]

    _sign_in(client, ORGANIZER)
    foreign = client.get(f"/organizer/search?q=מחווה&tender_id={foreign_id}")
    foreign_body = foreign.get_data(as_text=True)
    assert foreign.status_code == 403
    assert "ספק ז" not in foreign_body
    assert "הצעה זרה" not in foreign_body

    still_open = client.get(f"/organizer/search?q=מחווה&tender_id={open_id}")
    assert still_open.status_code == 403
    assert "קטע מחווה" not in still_open.get_data(as_text=True)

    missing = client.get("/organizer/search?q=מחווה&tender_id=missing-tender")
    assert missing.status_code == 404
    assert searcher.calls == []


def test_search_page_explains_an_empty_query_no_hits_and_a_down_port(client, backend):
    from app.domain.user import ORGANIZER
    from tests.test_tenders import _sign_in

    store, _users, tenders, bids = backend
    tender_id = _open(store, tenders)
    _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    searcher = RecordingSearch([])
    client.application.extensions["bid_search"] = searcher
    _sign_in(client, ORGANIZER)

    blank = client.get("/organizer/search", query_string={"q": "   "}).get_data(as_text=True)
    assert "הקלידו טקסט לחיפוש" in blank
    assert searcher.calls == []

    none = client.get("/organizer/search?q=מחווה").get_data(as_text=True)
    assert "לא נמצאו הצעות תואמות" in none
    assert searcher.calls == [("מחווה", [tender_id], 5)]

    class RaisingSearch:
        def __init__(self) -> None:
            self.calls = 0

        def search_bids(self, query: str, tender_ids: list[str], top_k: int = 5):
            self.calls += 1
            raise RuntimeError("chroma down")

    raising = RaisingSearch()
    client.application.extensions["bid_search"] = raising
    down = client.get("/organizer/search?q=מחווה").get_data(as_text=True)
    assert "החיפוש אינו זמין כרגע" in down
    assert "ספק א" not in down
    assert raising.calls == 1


def test_dashboard_name_filter_does_not_search_bids(client, backend):
    from app.domain.user import ORGANIZER
    from tests.test_tenders import _sign_in

    store, _users, tenders, bids = backend
    tender_id = _open(store, tenders)
    _offer(store, tenders, bids, tender_id, "sup-1", "ספק א", 70_000_000, PUBLISHED_AT)
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ORGANIZER_ID, explicit=True),
        now=NOW,
    )
    searcher = RecordingSearch([_Hit("bid", tender_id, "קטע מחווה")])
    client.application.extensions["bid_search"] = searcher
    _sign_in(client, ORGANIZER)
    matched = client.get("/organizer/?q=פרסים").get_data(as_text=True)
    assert "טקס פרסים" in matched
    missed = client.get("/organizer/?q=אין-כזה").get_data(as_text=True)
    assert "טקס פרסים" not in missed
    assert "אין מכרזים" in missed
    assert searcher.calls == []


def _open_for(store, tenders, organizer_id: str, name: str) -> str:
    return publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=organizer_id,
            name=name,
            event_type=CULTURE,
            event_date=EVENT_DAY,
            region="חיפה",
            budget_amount=BUDGET,
            deadline=DEADLINE,
            alpha=Decimal("0.6"),
            requirements=(RequirementSpec(VENUE, 100, True, "אולם"),),
            publish=True,
        ),
        now=PUBLISHED_AT,
    )
