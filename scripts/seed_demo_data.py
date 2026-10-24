"""Phase 5 demo tenders, scored through the command API.

    python scripts/seed_demo_data.py

Streams are fixed (`demo-tender-music`, `demo-tender-marathon`), so a second
run leaves the events in place. Prices are agorot. The earlier seed's open
"פסטיבל מוזיקה בים" is a different stream and stays open for bids.

Music is closed and fully assessed, with prices still sealed (INV-2, INV-4).
At its α of 0.60 the cheap qualified bid would rank first; above about 0.64
the expensive bid would. The marathon is revealed at α 0.80, so the expensive
bid ranks first and the comparison table is ready for a winner choice.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import bootstrap

bootstrap.init()

from app.commands.close_tender import CloseTender, close_tender  # noqa: E402
from app.commands.complete_assessment import (  # noqa: E402
    BidDisqualified,
    QualityAssessmentCompleted,
    RequirementScore,
    SqlCompleteAssessmentHandler,
)
from app.commands.publish_tender import PublishTender, RequirementSpec, publish_tender  # noqa: E402
from app.commands.register_user import RegisterUser, register_user  # noqa: E402
from app.commands.reveal_prices import RevealPrices, reveal_prices  # noqa: E402
from app.commands.score_requirement import ScoreRequirement, SqlScoreRequirementHandler  # noqa: E402
from app.commands.submit_bid import BidResponse, SubmitBid, submit_bid  # noqa: E402
from app.domain.scoring import below_quality_floor, fails_threshold, quality  # noqa: E402
from app.domain.tender import (  # noqa: E402
    CONTENT,
    CULTURE,
    EXPERIENCE,
    HOST,
    MUSIC,
    SPORTS,
    UNDER_EVALUATION,
    VENUE,
    event_day_end,
    israel_today,
)
from app.domain.user import SUPPLIER  # noqa: E402

PASSWORD = "Bidstage1"
RUBRIC_VERSION = "1"

MUSIC_TENDER_ID = "demo-tender-music"
MARATHON_TENDER_ID = "demo-tender-marathon"

MUSIC_VENUE = "demo-req-music-venue"
MUSIC_STRONG = "demo-bid-music-strong"
MUSIC_CHEAP = "demo-bid-music-cheap"
MUSIC_THRESHOLD = "demo-bid-music-threshold"
MUSIC_FLOOR = "demo-bid-music-floor"

MARATHON_STRONG = "demo-bid-marathon-strong"
MARATHON_CHEAP = "demo-bid-marathon-cheap"
MARATHON_MID = "demo-bid-marathon-mid"

_TYPES = (VENUE, CONTENT, HOST, MUSIC, EXPERIENCE)
_WEIGHTS = (25, 25, 15, 15, 20)
_LABELS = {
    VENUE: "המקום",
    CONTENT: "התוכן",
    HOST: "המנחה",
    MUSIC: "המוזיקה",
    EXPERIENCE: "החוויה",
}
_SOURCES = {
    VENUE: ("https://www.tel-aviv.gov.il/",),
    CONTENT: ("https://www.gov.il/",),
    HOST: ("https://www.tel-aviv.gov.il/",),
    MUSIC: ("https://www.gov.il/",),
    EXPERIENCE: ("https://www.gov.il/",),
}

_ORGANIZERS = (
    ("dana@seed.bidstage", "דנה לוי"),
    ("yossi@seed.bidstage", "יוסי מזרחי"),
)
_EXTRA_SUPPLIERS = (
    ("daniel@seed.bidstage", "דניאל כהן"),
    ("ron@seed.bidstage", "רון לוי"),
)
_KNOWN_SUPPLIERS = (
    ("noa@seed.bidstage", "נועה ברק"),
    ("avi@seed.bidstage", "אבי חדד"),
)


@dataclass(frozen=True)
class _BidPlan:
    bid_id: str
    supplier_email: str
    concept: str
    price_shekels: int
    scores: tuple[tuple[str, int], ...]
    outcome: str
    answers: dict[str, str]


@dataclass(frozen=True)
class _TenderPlan:
    tender_id: str
    owner_email: str
    name: str
    event_type: str
    region: str
    alpha: Decimal
    budget_agorot: int
    requirements: tuple[RequirementSpec, ...]
    bids: tuple[_BidPlan, ...]
    reveal: bool


def _requirements(prefix: str, descriptions: tuple[str, ...]) -> tuple[RequirementSpec, ...]:
    return tuple(
        RequirementSpec(
            requirement_type,
            weight,
            requirement_type == VENUE,
            description,
            f"{prefix}-{requirement_type.lower()}",
        )
        for requirement_type, weight, description in zip(
            _TYPES, _WEIGHTS, descriptions, strict=True
        )
    )


def _answers(venue: str, content: str, host: str, music: str, experience: str) -> dict[str, str]:
    return {
        VENUE: venue,
        CONTENT: content,
        HOST: host,
        MUSIC: music,
        EXPERIENCE: experience,
    }


def _marks(*scores: int) -> tuple[tuple[str, int], ...]:
    if len(scores) == 1:
        return ((VENUE, scores[0]),)
    return tuple(zip(_TYPES, scores, strict=True))


_MUSIC = _TenderPlan(
    tender_id=MUSIC_TENDER_ID,
    owner_email="dana@seed.bidstage",
    name="פסטיבל מוזיקה בים",
    event_type=CULTURE,
    region="תל אביב",
    alpha=Decimal("0.60"),
    budget_agorot=150_000_000,
    requirements=_requirements(
        "demo-req-music",
        (
            "רחבת חוף ל-3,000 צופים עם במה מקורה",
            "מופע מרכזי של 90 דקות על קו המים",
            "מנחה בעברית לאורך הערב",
            "תזמורת חיה והרכב אורח",
            "מתחם משפחות ופינות ישיבה",
        ),
    ),
    bids=(
        _BidPlan(
            MUSIC_STRONG,
            "noa@seed.bidstage",
            "הפקה מלאה: במה ימית, מנחה מוכר ותזמורת חיה",
            950_000,
            _marks(9, 9, 9, 9, 8),
            "qualified",
            _answers(
                "במת חוף מקורה ל-3,500 צופים עם רישיון עירייה",
                "מופע של 90 דקות עם אורח מרכזי",
                "מנחה בעברית לכל אורך הערב",
                "תזמורת חיה בת 12 נגנים והרכב אורח",
                "מתחם משפחות, פינות ישיבה ועמדות מים",
            ),
        ),
        _BidPlan(
            MUSIC_CHEAP,
            "avi@seed.bidstage",
            "הפקה חסכונית על במה קיימת עם צוות מקומי",
            620_000,
            _marks(7, 7, 7, 7, 6),
            "qualified",
            _answers(
                "במה קיימת על החוף לכ-3,000 צופים",
                "מופע של 90 דקות בלי אורח ראשי",
                "מנחה מקומי בעברית",
                "הרכב מקומי של שישה נגנים",
                "ישיבה על החול ועמדת מים אחת",
            ),
        ),
        _BidPlan(
            MUSIC_THRESHOLD,
            "daniel@seed.bidstage",
            "מתחם חלופי קטן מהקיבולת הנדרשת",
            480_000,
            _marks(4),
            "threshold",
            _answers(
                "רחבה ל-800 צופים בלבד, בלי במה מקורה",
                "מופע קצר של 40 דקות",
                "אין מנחה קבוע",
                "מוזיקה מוקלטת",
                "אין מתחם משפחות",
            ),
        ),
        _BidPlan(
            MUSIC_FLOOR,
            "ron@seed.bidstage",
            "הפקה חלקית בלי ניסיון מוכח ובלי לוח מופעים מלא",
            700_000,
            _marks(6, 6, 4, 4, 5),
            "quality_floor",
            _answers(
                "רחבה ל-3,000 צופים בלי כיסוי מלא לבמה",
                "לוח מופעים חלקי בלבד",
                "מנחה לחלק מהערב",
                "נגן יחיד בלי תזמורת",
                "פינת ישיבה קטנה בלי עמדות מים",
            ),
        ),
    ),
    reveal=False,
)

_MARATHON = _TenderPlan(
    tender_id=MARATHON_TENDER_ID,
    owner_email="yossi@seed.bidstage",
    name="מרתון תל אביב",
    event_type=SPORTS,
    region="תל אביב",
    alpha=Decimal("0.80"),
    budget_agorot=200_000_000,
    requirements=_requirements(
        "demo-req-marathon",
        (
            "מסלול עירוני סגור לתנועה ל-10 ק״מ",
            "שידור ולוח תוצאות לאורך המסלול",
            "קריין בקו הזינוק ובסיום",
            "מוזיקת חימום בקו הזינוק",
            "עמדות מים, עידוד וסיום מסודר",
        ),
    ),
    bids=(
        _BidPlan(
            MARATHON_STRONG,
            "noa@seed.bidstage",
            "הפקה מלאה של מסלול, שידור וצוות מסלול",
            1_100_000,
            _marks(9, 9, 9, 9, 8),
            "qualified",
            _answers(
                "מסלול 10 ק״מ סגור באישור משטרה",
                "שידור חי ולוח תוצאות בכל קילומטר",
                "קריין בזינוק ובסיום",
                "תזמורת חימום בקו הזינוק",
                "עמדת מים בכל 2.5 ק״מ וקשת סיום",
            ),
        ),
        _BidPlan(
            MARATHON_CHEAP,
            "avi@seed.bidstage",
            "מסלול בסיסי עם צוות מצומצם",
            740_000,
            _marks(7, 7, 7, 7, 6),
            "qualified",
            _answers(
                "מסלול 10 ק״מ עם חסימות חלקיות",
                "לוח תוצאות בסיום בלבד",
                "קריין בקו הזינוק",
                "מוזיקה מוקלטת בזינוק",
                "שתי עמדות מים לאורך המסלול",
            ),
        ),
        _BidPlan(
            MARATHON_MID,
            "daniel@seed.bidstage",
            "הפקה בינונית עם שידור חלקי",
            890_000,
            _marks(8, 8, 7, 7, 7),
            "qualified",
            _answers(
                "מסלול 10 ק״מ סגור לתנועה",
                "שידור בקטעים ולוח תוצאות בסיום",
                "קריין בזינוק",
                "הרכב קטן בקו הזינוק",
                "עמדת מים בכל 5 ק״מ",
            ),
        ),
    ),
    reveal=True,
)

_PLANS = (_MUSIC, _MARATHON)


@dataclass(frozen=True)
class DemoSeedResult:
    created_tenders: tuple[str, ...]
    skipped_tenders: tuple[str, ...]
    supplier_ids: dict[str, str]


def seed_demo_data(store, users, tenders, bids, conn, *, now: datetime | None = None) -> DemoSeedResult:
    """Append the demo streams once. An existing stream is left untouched."""
    clock = _utc(now)
    supplier_ids = _supplier_ids(store, users)
    created: list[str] = []
    skipped: list[str] = []
    for plan in _PLANS:
        if store.last_version(plan.tender_id):
            skipped.append(plan.tender_id)
            continue
        _seed_tender(store, users, tenders, bids, conn, plan, supplier_ids, clock)
        created.append(plan.tender_id)
    return DemoSeedResult(tuple(created), tuple(skipped), supplier_ids)


def verify_demo_data(conn) -> list[str]:
    """Read the projections and return problems. Empty means the scenarios landed."""
    problems: list[str] = []
    music = _tender_row(conn, MUSIC_TENDER_ID)
    marathon = _tender_row(conn, MARATHON_TENDER_ID)
    if music is None or music["status"] != UNDER_EVALUATION:
        problems.append(f"{MUSIC_TENDER_ID} is not under evaluation")
    if marathon is None or marathon["status"] != UNDER_EVALUATION:
        problems.append(f"{MARATHON_TENDER_ID} is not under evaluation")
    if music and Decimal(str(music["alpha"])) != Decimal("0.60"):
        problems.append(f"music alpha is {music['alpha']}")
    if marathon and Decimal(str(marathon["alpha"])) != Decimal("0.80"):
        problems.append(f"marathon alpha is {marathon['alpha']}")

    _expect_requirements(conn, problems, MUSIC_TENDER_ID, "demo-req-music")
    _expect_requirements(conn, problems, MARATHON_TENDER_ID, "demo-req-marathon")
    _expect_assessment(
        conn,
        problems,
        MUSIC_STRONG,
        "qualified",
        88.0,
        None,
        None,
        5,
    )
    _expect_assessment(conn, problems, MUSIC_CHEAP, "qualified", 68.0, None, None, 5)
    _expect_assessment(
        conn,
        problems,
        MUSIC_THRESHOLD,
        "disqualified",
        None,
        "threshold",
        MUSIC_VENUE,
        1,
    )
    _expect_assessment(
        conn,
        problems,
        MUSIC_FLOOR,
        "disqualified",
        52.0,
        "quality_floor",
        None,
        5,
    )
    for bid_id, expected in (
        (MARATHON_STRONG, 88.0),
        (MARATHON_CHEAP, 68.0),
        (MARATHON_MID, 75.0),
    ):
        _expect_assessment(conn, problems, bid_id, "qualified", expected, None, None, 5)

    if _score(conn, MUSIC_THRESHOLD, MUSIC_VENUE) != 4:
        problems.append("threshold bid did not record VENUE score 4")

    for bid_id in (
        MUSIC_STRONG,
        MUSIC_CHEAP,
        MUSIC_THRESHOLD,
        MUSIC_FLOOR,
        MARATHON_STRONG,
        MARATHON_CHEAP,
        MARATHON_MID,
    ):
        if _queue_status(conn, bid_id) != "completed":
            problems.append(f"{bid_id} is not completed on the assessment queue")

    if _prices_revealed(conn, MUSIC_TENDER_ID):
        problems.append("music prices were revealed")
    for bid_id in (MUSIC_STRONG, MUSIC_CHEAP, MUSIC_THRESHOLD, MUSIC_FLOOR):
        row = _ranking(conn, bid_id)
        if row is None or row["price_amount"] is not None:
            problems.append(f"{bid_id} price is visible before reveal")

    if not _prices_revealed(conn, MARATHON_TENDER_ID):
        problems.append("marathon prices were not revealed")
    strong = _ranking(conn, MARATHON_STRONG)
    cheap = _ranking(conn, MARATHON_CHEAP)
    mid = _ranking(conn, MARATHON_MID)
    if strong is None or cheap is None or mid is None:
        problems.append("marathon ranking is missing a bid")
    elif not (strong["rank"] == 1 and mid["rank"] == 2 and cheap["rank"] == 3):
        problems.append(
            "marathon rank is "
            f"{strong['rank']}, {mid['rank']}, {cheap['rank']} "
            "for strong, mid, cheap"
        )
    elif strong["price_amount"] != 110_000_000 or cheap["price_amount"] != 74_000_000:
        problems.append("marathon prices were not stored in agorot")
    return problems


def _seed_tender(store, users, tenders, bids, conn, plan: _TenderPlan, supplier_ids, clock: datetime) -> None:
    organizer_id = _organizer_id(users, plan.owner_email)
    event_date = israel_today(clock) + timedelta(days=60)
    deadline = event_day_end(event_date) - timedelta(hours=6)
    publish_tender(
        store,
        tenders,
        PublishTender(
            organizer_id=organizer_id,
            name=plan.name,
            event_type=plan.event_type,
            event_date=event_date,
            region=plan.region,
            budget_amount=plan.budget_agorot,
            deadline=deadline,
            alpha=plan.alpha,
            requirements=plan.requirements,
            publish=True,
            assigned_id=plan.tender_id,
        ),
        now=clock,
    )
    requirements = tenders.requirements_for(plan.tender_id)
    for index, bid in enumerate(plan.bids):
        _assert_scores(bid)
        supplier = users.get_by_email(bid.supplier_email)
        submit_bid(
            store,
            bids,
            SubmitBid(
                tender_id=plan.tender_id,
                supplier_id=supplier_ids[bid.supplier_email],
                concept=bid.concept,
                responses=tuple(
                    BidResponse(item.requirement_id, bid.answers[item.requirement_type])
                    for item in requirements
                ),
                price_amount=bid.price_shekels * 100,
                supplier_email=bid.supplier_email,
                supplier_display_name=supplier.display_name,
                bid_id=bid.bid_id,
            ),
            now=clock + timedelta(seconds=index),
            tenders=tenders,
        )
    close_tender(
        store,
        tenders,
        bids,
        CloseTender(tender_id=plan.tender_id, organizer_id=organizer_id, explicit=True),
        now=clock + timedelta(minutes=1),
    )
    for bid in plan.bids:
        _assess(conn, bid, requirements)
    if plan.reveal:
        reveal_prices(
            store,
            tenders,
            bids,
            RevealPrices(tender_id=plan.tender_id, organizer_id=organizer_id),
            now=clock + timedelta(minutes=2),
        )


def _assert_scores(bid: _BidPlan) -> None:
    by_type = dict(bid.scores)
    if bid.outcome == "threshold":
        if bid.scores != ((VENUE, 4),) or not fails_threshold(4):
            raise RuntimeError(f"{bid.bid_id} must fail VENUE with score 4")
        return
    if tuple(by_type) != _TYPES:
        raise RuntimeError(f"{bid.bid_id} must score every requirement")
    scored = quality(tuple((weight, by_type[kind]) for kind, weight in zip(_TYPES, _WEIGHTS, strict=True)))
    if bid.outcome == "qualified" and (below_quality_floor(scored) or fails_threshold(by_type[VENUE])):
        raise RuntimeError(f"{bid.bid_id} quality {scored} is not a qualified bid")
    if bid.outcome == "quality_floor" and not below_quality_floor(scored):
        raise RuntimeError(f"{bid.bid_id} quality {scored} is not below the floor")
    expected = {
        MUSIC_STRONG: 88.0,
        MUSIC_CHEAP: 68.0,
        MUSIC_FLOOR: 52.0,
        MARATHON_STRONG: 88.0,
        MARATHON_CHEAP: 68.0,
        MARATHON_MID: 75.0,
    }
    if scored != expected[bid.bid_id]:
        raise RuntimeError(f"{bid.bid_id} quality is {scored}, expected {expected[bid.bid_id]}")


def _assess(conn, bid: _BidPlan, requirements) -> None:
    by_type = {item.requirement_type: item for item in requirements}
    scorer = SqlScoreRequirementHandler(conn)
    recorded: list[RequirementScore] = []
    for requirement_type, score in bid.scores:
        requirement = by_type[requirement_type]
        scorer.handle(
            ScoreRequirement(
                bid_id=bid.bid_id,
                requirement_id=requirement.requirement_id,
                score=score,
                justification=_justification(requirement_type, score),
                sources=_SOURCES[requirement_type],
                rubric_version=RUBRIC_VERSION,
            )
        )
        recorded.append(RequirementScore(requirement.requirement_id, score, requirement.weight))
    complete = SqlCompleteAssessmentHandler(conn)
    if bid.outcome == "threshold":
        complete.handle(
            BidDisqualified(
                bid.bid_id,
                "threshold",
                failed_requirement_id=by_type[VENUE].requirement_id,
            )
        )
        return
    quality_score = quality(tuple((item.weight, item.score) for item in recorded))
    if bid.outcome == "quality_floor":
        complete.handle(BidDisqualified(bid.bid_id, "quality_floor", quality_score=quality_score))
        return
    complete.handle(QualityAssessmentCompleted(bid.bid_id, quality_score, tuple(recorded)))


def _justification(requirement_type: str, score: int) -> str:
    label = _LABELS[requirement_type]
    if score >= 9:
        return f"{label} עומד בדרישה במלואה, עם ראיה שניתן לאמת."
    if score >= 7:
        return f"{label} עומד בדרישה, והראיות אומתו חלקית."
    if score >= 6:
        return f"{label} עומד בדרישה באופן כללי, עם פער מהותי."
    return f"{label} חסר במרכיב מרכזי של הדרישה."


def _supplier_ids(store, users) -> dict[str, str]:
    ids: dict[str, str] = {}
    for email, _name in _KNOWN_SUPPLIERS + _EXTRA_SUPPLIERS:
        record = users.get_by_email(email)
        if record is None:
            if email not in {item[0] for item in _EXTRA_SUPPLIERS}:
                raise RuntimeError(f"missing supplier {email}; run seed_db.py first")
            continue
        if record.role != SUPPLIER:
            raise RuntimeError(f"{email} is registered as {record.role}")
        ids[email] = record.user_id
    for email, display_name in _EXTRA_SUPPLIERS:
        if email in ids:
            continue
        ids[email] = register_user(
            store,
            users,
            RegisterUser(
                email=email,
                password=PASSWORD,
                display_name=display_name,
                role=SUPPLIER,
            ),
        )
    return ids


def _organizer_id(users, email: str) -> str:
    record = users.get_by_email(email)
    expected = dict(_ORGANIZERS)[email]
    if record is None:
        raise RuntimeError(f"missing organizer {email} ({expected}); run seed_db.py first")
    return record.user_id


def _expect_requirements(conn, problems: list[str], tender_id: str, prefix: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT requirement_id, requirement_type, weight, is_threshold "
        "FROM dbo.rm_requirement WHERE tender_id = ? ORDER BY sort_order",
        tender_id,
    )
    rows = cursor.fetchall()
    cursor.close()
    found = [(str(row[1]), int(row[2]), bool(row[3]), str(row[0])) for row in rows]
    expected = [
        (kind, weight, kind == VENUE, f"{prefix}-{kind.lower()}")
        for kind, weight in zip(_TYPES, _WEIGHTS, strict=True)
    ]
    if found != expected:
        problems.append(f"{tender_id} requirements are {found}")


def _expect_assessment(
    conn,
    problems: list[str],
    bid_id: str,
    status: str,
    quality_score: float | None,
    reason: str | None,
    failed_requirement_id: str | None,
    score_rows: int,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status, quality_score, reason, failed_requirement_id "
        "FROM dbo.rm_bid_assessment WHERE bid_id = ?",
        bid_id,
    )
    row = cursor.fetchone()
    cursor.execute(
        "SELECT COUNT(*) FROM dbo.rm_requirement_score WHERE bid_id = ?",
        bid_id,
    )
    count = int(cursor.fetchone()[0])
    cursor.close()
    if row is None:
        problems.append(f"{bid_id} has no assessment row")
        return
    if str(row[0]) != status or (None if row[2] is None else str(row[2])) != reason:
        problems.append(f"{bid_id} assessment is {row[0]!r} / {row[2]!r}")
    actual_failed = None if row[3] is None else str(row[3])
    if actual_failed != failed_requirement_id:
        problems.append(f"{bid_id} failed requirement is {actual_failed}")
    if quality_score is None:
        if row[1] is not None:
            problems.append(f"{bid_id} quality should be empty")
    elif row[1] is None or abs(float(row[1]) - quality_score) > 0.001:
        problems.append(f"{bid_id} quality is {row[1]}")
    if count != score_rows:
        problems.append(f"{bid_id} has {count} requirement scores")


def _score(conn, bid_id: str, requirement_id: str) -> int | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT score FROM dbo.rm_requirement_score WHERE bid_id = ? AND requirement_id = ?",
        bid_id,
        requirement_id,
    )
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        return None
    return int(row[0])


def _queue_status(conn, bid_id: str) -> str | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status FROM dbo.rm_bid_for_assessment WHERE bid_id = ?",
        bid_id,
    )
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        return None
    return str(row[0])


def _ranking(conn, bid_id: str) -> dict | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT price_amount, final_score, rank FROM dbo.rm_bid_ranking WHERE bid_id = ?",
        bid_id,
    )
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        return None
    return {
        "price_amount": None if row[0] is None else int(row[0]),
        "final_score": None if row[1] is None else row[1],
        "rank": None if row[2] is None else int(row[2]),
    }


def _prices_revealed(conn, tender_id: str) -> bool:
    from app.events.event_store import EventStore
    from app.events.event_types import PRICES_REVEALED

    return any(
        event.event_type == PRICES_REVEALED
        for event in EventStore(conn).read_stream(tender_id)
    )


def _tender_row(conn, tender_id: str) -> dict | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, status, alpha FROM dbo.rm_tender_detail WHERE tender_id = ?",
        tender_id,
    )
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        return None
    return {"name": str(row[0]), "status": str(row[1]), "alpha": row[2]}


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _format_result(result: DemoSeedResult, problems: list[str]) -> str:
    lines = [
        f"created: {', '.join(result.created_tenders) or '—'}",
        f"skipped: {', '.join(result.skipped_tenders) or '—'}",
    ]
    for email, _name in _EXTRA_SUPPLIERS:
        lines.append(f"supplier {email} {result.supplier_ids.get(email, 'missing')}")
    if problems:
        lines.append("problems:")
        lines.extend(f"- {problem}" for problem in problems)
    else:
        lines.append("scenarios: ok")
    return "\n".join(lines)


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
            result = seed_demo_data(
                EventStore(conn),
                SqlUsersProjection(conn),
                SqlTendersProjection(conn),
                SqlBidsProjection(conn),
                conn,
            )
        with transaction() as conn:
            problems = verify_demo_data(conn)
    except pyodbc.Error as exc:
        print(f"Database error: {exc}")
        return 1
    except RuntimeError as exc:
        print(exc)
        return 1
    print(_format_result(result, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
