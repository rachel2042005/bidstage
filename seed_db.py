"""Sample organizers, suppliers, tenders, and sealed bids.

Writes only through the command API. A second run finds the seed emails and
leaves those accounts and tenders in place, then prints the same summary.
"""

from __future__ import annotations

from app import bootstrap

bootstrap.init()

from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from app.commands.close_tender import CloseTender, close_tender  # noqa: E402
from app.commands.publish_tender import PublishTender, RequirementSpec, publish_tender  # noqa: E402
from app.commands.register_user import RegisterUser, register_user  # noqa: E402
from app.commands.submit_bid import BidResponse, SubmitBid, submit_bid  # noqa: E402
from app.domain.tender import (  # noqa: E402
    CONTENT,
    CULTURE,
    EXPERIENCE,
    HOST,
    MUSIC,
    SPORTS,
    VENUE,
    event_day_end,
    israel_today,
)
from app.domain.user import ORGANIZER, SUPPLIER  # noqa: E402
from app.projections.tenders import TenderSearch  # noqa: E402

PASSWORD = "Bidstage1"

_FESTIVAL = (
    RequirementSpec(VENUE, 30, True, "רחבת חוף ל-2,000 צופים עם במה מקורה"),
    RequirementSpec(CONTENT, 20, False, "מופע מרכזי של 90 דקות"),
    RequirementSpec(HOST, 15, True, "מנחה בעברית לאורך הערב"),
    RequirementSpec(MUSIC, 20, False, "תזמורת חיה"),
    RequirementSpec(EXPERIENCE, 15, False, "מתחם משפחות ופינות ישיבה"),
)
_CONCERT = (
    RequirementSpec(VENUE, 25, True, "במת חוץ מקורה בפארק"),
    RequirementSpec(CONTENT, 25, False, "שני מופעים עם הפסקה"),
    RequirementSpec(HOST, 20, True, "מנחה מוכר לקהל הרחב"),
    RequirementSpec(MUSIC, 20, False, "הרכב אקוסטי"),
    RequirementSpec(EXPERIENCE, 10, False, "פינת ילדים בשולי הדשא"),
)
_MARATHON = (
    RequirementSpec(VENUE, 35, True, "מסלול עירוני סגור לתנועה"),
    RequirementSpec(CONTENT, 15, False, "שידור ולוח תוצאות לאורך המסלול"),
    RequirementSpec(HOST, 10, False, "קריין בקו הזינוק"),
    RequirementSpec(MUSIC, 10, False, "מוזיקת חימום בקו הזינוק"),
    RequirementSpec(EXPERIENCE, 30, False, "עמדות מים ועידוד"),
)
_TOURNAMENT = (
    RequirementSpec(VENUE, 30, True, "מגרש חול תקני לשני משחקים במקביל"),
    RequirementSpec(CONTENT, 20, False, "טקס פתיחה קצר"),
    RequirementSpec(HOST, 15, False, "שופט ראשי וצוות קו"),
    RequirementSpec(MUSIC, 10, False, "מוזיקת רקע בהפסקות"),
    RequirementSpec(EXPERIENCE, 25, False, "יציע ל-800 צופים"),
)
_BASKETBALL = (
    RequirementSpec(VENUE, 40, True, "אולם ספורט עם יציע ל-1,000 צופים"),
    RequirementSpec(CONTENT, 20, False, "ארבעה משחקי נוער ביום"),
    RequirementSpec(HOST, 15, True, "שדרן מקומי"),
    RequirementSpec(MUSIC, 10, False, "מוזיקת חימום בין רבעים"),
    RequirementSpec(EXPERIENCE, 15, False, "דוכן אוהדים ופינת חתימות"),
)


@dataclass(frozen=True)
class _Account:
    email: str
    display_name: str
    role: str


@dataclass(frozen=True)
class _TenderPlan:
    owner_email: str
    name: str
    event_type: str
    publish: bool
    region: str = ""
    budget_amount: int | None = None
    schedule: str = ""
    requirements: tuple[RequirementSpec, ...] = ()
    with_bids: bool = False


_ACCOUNTS = (
    _Account("dana@seed.bidstage", "דנה לוי", ORGANIZER),
    _Account("yossi@seed.bidstage", "יוסי מזרחי", ORGANIZER),
    _Account("michal@seed.bidstage", "מיכל אברהם", ORGANIZER),
    _Account("noa@seed.bidstage", "נועה ברק", SUPPLIER),
    _Account("avi@seed.bidstage", "אבי חדד", SUPPLIER),
)

_TENDERS = (
    _TenderPlan("dana@seed.bidstage", "טיוטת טקס עירוני", CULTURE, False),
    _TenderPlan(
        "yossi@seed.bidstage",
        "אליפות כדורסל נוער",
        SPORTS,
        False,
        region="ירושלים",
        budget_amount=30_000_000,
        schedule="far",
        requirements=_BASKETBALL,
    ),
    _TenderPlan(
        "dana@seed.bidstage",
        "פסטיבל מוזיקה בים",
        CULTURE,
        True,
        region="תל אביב",
        budget_amount=80_000_000,
        schedule="far",
        requirements=_FESTIVAL,
    ),
    _TenderPlan(
        "michal@seed.bidstage",
        "קונצרט בפארק",
        CULTURE,
        True,
        region="חיפה",
        budget_amount=60_000_000,
        schedule="far",
        requirements=_CONCERT,
    ),
    _TenderPlan(
        "yossi@seed.bidstage",
        "מרתון תל אביב",
        SPORTS,
        True,
        region="תל אביב",
        budget_amount=40_000_000,
        schedule="soon",
        requirements=_MARATHON,
    ),
    _TenderPlan(
        "michal@seed.bidstage",
        "טורניר כדורגל חופים",
        SPORTS,
        True,
        region="אילת",
        budget_amount=25_000_000,
        schedule="far",
        requirements=_TOURNAMENT,
        with_bids=True,
    ),
)

_BIDDERS = (
    ("noa@seed.bidstage", "הפקה מלאה לטורניר חופים, כולל במה, שיפוט וקהל", 12_000_000),
    ("avi@seed.bidstage", "הפקה קומפקטית עם צוות מקומי ומגרש חול מוכן", 9_500_000),
)

_ANSWERS = {
    VENUE: "המגרש עומד בתקן וזמין לכל ימי הטורניר",
    CONTENT: "טקס הפתיחה יופק על ידי הצוות שלנו",
    HOST: "שופט ראשי מוסמך ושני שופטי קו",
    MUSIC: "מערכת הגברה ומוזיקת רקע בהפסקות",
    EXPERIENCE: "יציע מוצל ל-800 צופים עם שירותים",
}


@dataclass(frozen=True)
class SeedTender:
    name: str
    event_type: str
    status: str


@dataclass(frozen=True)
class SeedUser:
    display_name: str
    role: str
    email: str
    user_id: str
    password: str
    tenders: tuple[SeedTender, ...]


@dataclass(frozen=True)
class SeedSummary:
    users: tuple[SeedUser, ...]


def seed_database(store, users, tenders, bids, *, now: datetime | None = None) -> SeedSummary:
    clock = _utc(now)
    if any(users.get_by_email(account.email) is not None for account in _ACCOUNTS):
        return _summary(users, tenders)
    ids = {
        account.email: register_user(
            store,
            users,
            RegisterUser(
                email=account.email,
                password=PASSWORD,
                display_name=account.display_name,
                role=account.role,
            ),
        )
        for account in _ACCOUNTS
    }
    for plan in _TENDERS:
        event_date, deadline = _schedule(clock, plan.schedule)
        tender_id = publish_tender(
            store,
            tenders,
            PublishTender(
                organizer_id=ids[plan.owner_email],
                name=plan.name,
                event_type=plan.event_type,
                event_date=event_date,
                region=plan.region,
                budget_amount=plan.budget_amount,
                deadline=deadline,
                requirements=plan.requirements,
                publish=plan.publish,
            ),
            now=clock,
        )
        if plan.with_bids:
            _seed_bids(store, tenders, bids, tender_id, ids, clock)
    return _summary(users, tenders)


def format_summary(summary: SeedSummary) -> str:
    header = ("Name", "Role", "Email", "Password", "User ID", "Tenders")
    rows = [header]
    for user in summary.users:
        owned = "; ".join(
            f"{item.event_type} / {item.status} / {item.name}" for item in user.tenders
        )
        rows.append(
            (
                user.display_name,
                user.role,
                user.email,
                user.password,
                user.user_id,
                owned or "—",
            )
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(header))]
    lines = [_format_row(row, widths) for row in rows]
    rule = "-+-".join("-" * width for width in widths)
    return "\n".join((lines[0], rule, *lines[1:]))


def _seed_bids(store, tenders, bids, tender_id: str, ids: dict[str, str], clock: datetime) -> None:
    requirements = tenders.requirements_for(tender_id)
    for email, concept, price in _BIDDERS:
        record = _account(email)
        submit_bid(
            store,
            bids,
            SubmitBid(
                tender_id=tender_id,
                supplier_id=ids[email],
                concept=concept,
                responses=tuple(
                    BidResponse(item.requirement_id, _ANSWERS[item.requirement_type])
                    for item in requirements
                ),
                price_amount=price,
                supplier_email=email,
                supplier_display_name=record.display_name,
            ),
            now=clock,
            tenders=tenders,
        )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=tender_id, organizer_id=ids["michal@seed.bidstage"], explicit=True),
        now=clock,
    )


def _summary(users, tenders) -> SeedSummary:
    rows = []
    for account in _ACCOUNTS:
        record = users.get_by_email(account.email)
        if record is None:
            continue
        owned = ()
        if record.role == ORGANIZER:
            found = tenders.search(TenderSearch(organizer_id=record.user_id))
            owned = tuple(
                SeedTender(name=item.name, event_type=item.event_type, status=item.status)
                for item in sorted(found, key=lambda item: item.name)
            )
        rows.append(
            SeedUser(
                display_name=record.display_name,
                role=record.role,
                email=record.email,
                user_id=record.user_id,
                password=PASSWORD,
                tenders=owned,
            )
        )
    return SeedSummary(users=tuple(rows))


def _schedule(now: datetime, kind: str):
    if not kind:
        return None, None
    today = israel_today(now)
    if kind == "soon":
        event_date = today + timedelta(days=4)
        deadline = event_day_end(event_date) - timedelta(hours=20)
    else:
        event_date = today + timedelta(days=45)
        deadline = event_day_end(event_date) - timedelta(hours=5)
    return event_date, deadline


def _account(email: str) -> _Account:
    return next(account for account in _ACCOUNTS if account.email == email)


def _format_row(row: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def main() -> int:
    import pyodbc

    from app.events.db import transaction
    from app.events.event_store import EventStore
    from app.events.schema import create_schema
    from app.projections.bids import SqlBidsProjection, create_bids_projection
    from app.projections.schema_sync import ensure_read_model_schema
    from app.projections.tenders import SqlTendersProjection, create_tenders_projection
    from app.projections.users import SqlUsersProjection, create_users_projection

    try:
        with transaction() as conn:
            create_schema(conn)
            create_users_projection(conn)
            create_tenders_projection(conn)
            create_bids_projection(conn)
            ensure_read_model_schema(conn)
            summary = seed_database(
                EventStore(conn),
                SqlUsersProjection(conn),
                SqlTendersProjection(conn),
                SqlBidsProjection(conn),
            )
    except pyodbc.Error as exc:
        print(f"Database error: {exc}")
        return 1
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
