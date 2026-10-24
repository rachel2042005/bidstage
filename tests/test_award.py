"""Override, award, and who may open the breakdown.

Seams: OverrideScore, SelectWinner, the organizer comparison page,
and the supplier's own bid page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.commands.close_tender import CloseTender, close_tender
from app.commands.override_score import OverrideScore, override_score
from app.commands.publish_tender import RequirementSpec, publish_tender
from app.commands.reveal_prices import RevealPrices, reveal_prices
from app.commands.select_winner import SelectWinner, select_winner
from app.domain.tender import (
    CONTENT,
    VENUE,
    BidNotQualifiedError,
    EmptyJustificationError,
    TenderAlreadyDecidedError,
)
from app.domain.user import ORGANIZER, SUPPLIER
from app.events.event_types import (
    BID_DISQUALIFIED,
    FINAL_RANKING_CALCULATED,
    SCORE_OVERRIDDEN,
    WINNER_SELECTED,
)
from app.projections.users import UserRecord
from app.queries.get_bid_comparison import get_bid_comparison
from app.queries.get_tender import get_tender_details
from tests.test_dashboard import PUBLISHED_AT, _append, _backend, _offer
from tests.test_tenders import _draft, _sign_in

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def test_override_rejects_an_empty_justification():
    store, _users, tenders, bids, tender_id, _bid_a, bid_b, content_id = _ranked()
    before_bid = [event.event_type for event in store.read_stream(bid_b)]
    before_tender = [event.event_type for event in store.read_stream(tender_id)]

    with pytest.raises(EmptyJustificationError):
        override_score(
            store,
            tenders,
            bids,
            OverrideScore(
                tender_id=tender_id,
                bid_id=bid_b,
                requirement_id=content_id,
                new_score=1,
                justification="   ",
                organizer_id="org-1",
            ),
            now=NOW,
        )

    assert [event.event_type for event in store.read_stream(bid_b)] == before_bid
    assert [event.event_type for event in store.read_stream(tender_id)] == before_tender


def test_override_that_disqualifies_a_bid_recomputes_every_price_score():
    store, _users, tenders, bids, tender_id, bid_a, bid_b, content_id = _ranked()
    ranked = get_bid_comparison(bids, tender_id, sort="final")
    assert [(row.bid_id, row.price_score, row.final_score) for row in ranked] == [
        (bid_b, Decimal("100.0"), Decimal("85.0")),
        (bid_a, Decimal("77.8"), Decimal("82.1")),
    ]

    override_score(
        store,
        tenders,
        bids,
        OverrideScore(
            tender_id=tender_id,
            bid_id=bid_b,
            requirement_id=content_id,
            new_score=1,
            justification="המענה אינו עומד בדרישה",
            organizer_id="org-1",
        ),
        now=NOW,
    )

    bid_events = store.read_stream(bid_b)
    assert [event.event_type for event in bid_events[-2:]] == [SCORE_OVERRIDDEN, BID_DISQUALIFIED]
    overridden = bid_events[-2].event_data
    assert overridden["old_score"] == 7
    assert overridden["new_score"] == 1
    assert overridden["justification"] == "המענה אינו עומד בדרישה"
    tender_events = store.read_stream(tender_id)
    assert tender_events[-1].event_type == FINAL_RANKING_CALCULATED
    assert tender_events[-1].event_data["ranking"] == [
        {
            "bid_id": bid_a,
            "quality": "85.0",
            "price": 90_000_000,
            "price_score": "100.0",
            "final_score": "91.0",
            "rank": 1,
        }
    ]
    after = get_bid_comparison(bids, tender_id, sort="final")
    assert [(row.bid_id, row.price_score, row.final_score, row.rank) for row in after] == [
        (bid_a, Decimal("100.0"), Decimal("91.0"), 1),
        (bid_b, None, None, None),
    ]


def test_select_winner_rejects_a_disqualified_bid_and_returns_both_emails():
    store, users, tenders, bids, tender_id, bid_a, bid_b, content_id = _ranked()
    _people(users)
    override_score(
        store,
        tenders,
        bids,
        OverrideScore(
            tender_id=tender_id,
            bid_id=bid_b,
            requirement_id=content_id,
            new_score=1,
            justification="המענה אינו עומד בדרישה",
            organizer_id="org-1",
        ),
        now=NOW,
    )
    before = [event.event_type for event in store.read_stream(tender_id)]

    with pytest.raises(BidNotQualifiedError):
        select_winner(
            store,
            tenders,
            bids,
            users,
            SelectWinner(tender_id=tender_id, bid_id=bid_b, organizer_id="org-1"),
            now=NOW,
        )
    assert [event.event_type for event in store.read_stream(tender_id)] == before

    notices = select_winner(
        store,
        tenders,
        bids,
        users,
        SelectWinner(tender_id=tender_id, bid_id=bid_a, organizer_id="org-1"),
        now=NOW,
    )
    assert store.read_stream(tender_id)[-1].event_type == WINNER_SELECTED
    assert store.read_stream(tender_id)[-1].event_data["bid_id"] == bid_a
    by_email = {notice.to_email: notice for notice in notices}
    assert set(by_email) == {"a@example.com", "b@example.com"}
    assert by_email["a@example.com"].won is True
    assert "נבחרה" in by_email["a@example.com"].body
    assert "לא נבחרה" in by_email["b@example.com"].body
    assert by_email["a@example.com"].subject != by_email["b@example.com"].subject
    for notice in notices:
        assert "₪" not in notice.body + notice.subject
        assert "900,000" not in notice.body
        assert "700,000" not in notice.body

    with pytest.raises(TenderAlreadyDecidedError):
        select_winner(
            store,
            tenders,
            bids,
            users,
            SelectWinner(tender_id=tender_id, bid_id=bid_a, organizer_id="org-1"),
            now=NOW,
        )
    with pytest.raises(TenderAlreadyDecidedError):
        override_score(
            store,
            tenders,
            bids,
            OverrideScore(
                tender_id=tender_id,
                bid_id=bid_a,
                requirement_id=content_id,
                new_score=6,
                justification="מאוחר מדי",
                organizer_id="org-1",
            ),
            now=NOW,
        )


def test_owner_can_override_and_award_and_others_get_403(client, backend):
    store, users, tenders, bids = backend
    _people(users)
    tender_id, bid_a, bid_b, content_id = _publish_rank(store, tenders, bids)
    _sign_in(client, ORGANIZER, user_id="org-1")
    page = client.get(f"/organizer/tenders/{tender_id}/comparison")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "דריסת ציון" in body
    assert "בחירת זוכה" in body
    assert 'class="agent-score">9' in body
    assert "פרטי המכרז" in body

    _sign_in(client, ORGANIZER, user_id="org-2")
    before = [event.event_type for event in store.read_stream(bid_b)]
    forbidden = client.post(
        f"/organizer/tenders/{tender_id}/bids/{bid_b}/score",
        data={"requirement_id": content_id, "new_score": "1", "justification": "לא בעל המכרז"},
    )
    assert forbidden.status_code == 403
    assert [event.event_type for event in store.read_stream(bid_b)] == before
    award = client.post(
        f"/organizer/tenders/{tender_id}/winner",
        data={"bid_id": bid_a},
    )
    assert award.status_code == 403
    assert WINNER_SELECTED not in [event.event_type for event in store.read_stream(tender_id)]

    _sign_in(client, SUPPLIER, user_id="sup-a")
    assert client.get(f"/organizer/tenders/{tender_id}/comparison").status_code == 403
    own = client.get(f"/supplier/bids/{bid_a}")
    own_body = own.get_data(as_text=True)
    assert own.status_code == 200
    assert 'class="agent-score">9' in own_body
    assert "דריסת ציון" not in own_body
    assert "700,000.00" not in own_body
    assert "900,000.00" in own_body
    other = client.get(f"/supplier/bids/{bid_b}")
    assert other.status_code == 403

    class _Boom:
        def send(self, to: str, subject: str, body: str) -> None:
            raise RuntimeError("gmail down")

    client.application.extensions["bid_notifier"] = _Boom()
    _sign_in(client, ORGANIZER, user_id="org-1")
    response = client.post(
        f"/organizer/tenders/{tender_id}/winner",
        data={"bid_id": bid_a},
    )
    assert response.status_code == 302
    assert store.read_stream(tender_id)[-1].event_type == WINNER_SELECTED


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


def _ranked():
    store, users, tenders, bids = _backend()
    tender_id, bid_a, bid_b, content_id = _publish_rank(store, tenders, bids)
    return store, users, tenders, bids, tender_id, bid_a, bid_b, content_id


def _publish_rank(store, tenders, bids):
    tender_id = publish_tender(
        store,
        tenders,
        _draft(
            requirements=(
                RequirementSpec(VENUE, 50, True, "אולם"),
                RequirementSpec(CONTENT, 50, False, "תוכן"),
            ),
            publish=True,
        ),
        now=PUBLISHED_AT,
    )
    details = get_tender_details(tenders, tender_id)
    assert details is not None
    venue_id = details.requirements[0].requirement_id
    content_id = details.requirements[1].requirement_id
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
        CloseTender(tender_id=tender_id, organizer_id="org-1", explicit=True),
        now=PUBLISHED_AT,
    )
    _score(store, bid_a, "85", venue_id, 9, content_id, 8)
    _score(store, bid_b, "75", venue_id, 8, content_id, 7)
    reveal_prices(
        store,
        tenders,
        bids,
        RevealPrices(tender_id=tender_id, organizer_id="org-1"),
        now=NOW,
    )
    return tender_id, bid_a, bid_b, content_id


def _score(store, bid_id, quality, venue_id, venue_score, content_id, content_score):
    from app.events.event_types import QUALITY_ASSESSMENT_COMPLETED

    _append(
        store,
        bid_id,
        QUALITY_ASSESSMENT_COMPLETED,
        {
            "quality_score": quality,
            "requirement_scores": [
                {
                    "requirement_id": venue_id,
                    "score": venue_score,
                    "weight": 50,
                    "is_threshold": True,
                },
                {
                    "requirement_id": content_id,
                    "score": content_score,
                    "weight": 50,
                    "is_threshold": False,
                },
            ],
        },
    )


def _people(users):
    for user_id, email, name in (
        ("sup-a", "a@example.com", "ספק א"),
        ("sup-b", "b@example.com", "ספק ב"),
    ):
        users.insert(
            UserRecord(
                user_id=user_id,
                email=email,
                display_name=name,
                role=SUPPLIER,
                password_hash="x",
            )
        )
