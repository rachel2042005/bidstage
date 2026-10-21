"""Seed summary: logins and the tenders each organizer owns.

Seam: seed_database(store, users, tenders, bids) -> summary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.events.memory import InMemoryEventStore
from app.projections.bids import InMemoryBidsProjection
from app.projections.tenders import InMemoryTendersProjection, TenderSearch
from app.projections.users import InMemoryUsersProjection
from seed_db import seed_database

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


def test_seed_summary_lists_logins_and_each_organizers_tenders():
    store = InMemoryEventStore()
    users = InMemoryUsersProjection()
    tenders = InMemoryTendersProjection()
    bids = InMemoryBidsProjection()

    summary = seed_database(store, users, tenders, bids, now=NOW)
    again = seed_database(store, users, tenders, bids, now=NOW)

    assert [(user.email, user.user_id) for user in again.users] == [
        (user.email, user.user_id) for user in summary.users
    ]
    assert len(tenders.search(TenderSearch())) == 6

    by_email = {user.email: user for user in summary.users}
    assert set(by_email) == {
        "dana@seed.bidstage",
        "yossi@seed.bidstage",
        "michal@seed.bidstage",
        "noa@seed.bidstage",
        "avi@seed.bidstage",
    }
    assert all(user.password == "Bidstage1" for user in summary.users)

    dana = by_email["dana@seed.bidstage"]
    yossi = by_email["yossi@seed.bidstage"]
    michal = by_email["michal@seed.bidstage"]
    noa = by_email["noa@seed.bidstage"]
    avi = by_email["avi@seed.bidstage"]

    assert (dana.display_name, dana.role) == ("דנה לוי", "organizer")
    assert (yossi.display_name, yossi.role) == ("יוסי מזרחי", "organizer")
    assert (michal.display_name, michal.role) == ("מיכל אברהם", "organizer")
    assert (noa.display_name, noa.role) == ("נועה ברק", "supplier")
    assert (avi.display_name, avi.role) == ("אבי חדד", "supplier")
    assert noa.tenders == ()
    assert avi.tenders == ()

    assert _owned(dana) == {
        ("טיוטת טקס עירוני", "culture", "draft"),
        ("פסטיבל מוזיקה בים", "culture", "open"),
    }
    assert _owned(yossi) == {
        ("אליפות כדורסל נוער", "sports", "draft"),
        ("מרתון תל אביב", "sports", "open"),
    }
    assert _owned(michal) == {
        ("קונצרט בפארק", "culture", "open"),
        ("טורניר כדורגל חופים", "sports", "under_evaluation"),
    }

    rows = {row.name: row for row in tenders.search(TenderSearch())}
    sparse = rows["טיוטת טקס עירוני"]
    assert sparse.event_date is None
    assert sparse.deadline is None
    assert sparse.region == ""
    assert sparse.budget_amount == 0
    assert tenders.requirements_for(sparse.tender_id) == []

    filled = rows["אליפות כדורסל נוער"]
    assert filled.region == "ירושלים"
    assert filled.budget_amount == 30_000_000
    assert len(tenders.requirements_for(filled.tender_id)) == 5

    closed = rows["טורניר כדורגל חופים"]
    assert bids.count_for(closed.tender_id) == 2
    assert {row.price_amount for row in bids.comparison_for(closed.tender_id)} == {None}
    for row in rows.values():
        if row.name != closed.name:
            assert bids.count_for(row.tender_id) == 0

    supplier_tenders = {
        bid.tender_name
        for supplier in (noa, avi)
        for bid in bids.for_supplier(supplier.user_id)
    }
    assert supplier_tenders == {"טורניר כדורגל חופים"}


def _owned(user) -> set[tuple[str, str, str]]:
    return {(tender.name, tender.event_type, tender.status) for tender in user.tenders}
