"""Bid read models.

rm_bid_for_assessment is the agent's later work queue. It has concept and
responses and no price column (NFR-AGT-4). rm_supplier_bid is the supplier's
own list. rm_dashboard stores the organizer's bid count. None of them stores
the sealed price.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

import pyodbc

from app.domain.bid import DISQUALIFIED, QUALIFIED, SUBMITTED, WITHDRAWN
from app.domain.user import UnknownEventTypeError
from app.events.event_types import (
    BID_DISQUALIFIED,
    BID_EVENTS,
    BID_SUBMITTED,
    BID_WITHDRAWN,
    FINAL_RANKING_CALCULATED,
    PRICE_OFFER_SEALED,
    PRICES_REVEALED,
    QUALITY_ASSESSMENT_COMPLETED,
    REQUIREMENT_SCORED,
    SCORE_OVERRIDDEN,
    TENDER_CREATED,
    TENDER_DETAILS_REVISED,
)


@dataclass(frozen=True)
class ResponseView:
    requirement_id: str
    answer: str


@dataclass(frozen=True)
class AssessmentView:
    bid_id: str
    tender_id: str
    concept: str
    responses: tuple[ResponseView, ...]


@dataclass(frozen=True)
class SupplierBidView:
    bid_id: str
    tender_id: str
    supplier_id: str
    tender_name: str
    status: str
    submitted_at: datetime


@dataclass(frozen=True)
class ComparisonRow:
    bid_id: str
    tender_id: str
    supplier_id: str
    supplier_name: str
    status: str
    submitted_at: datetime
    quality: Decimal | None = None
    price_amount: int | None = None
    price_score: Decimal | None = None
    final_score: Decimal | None = None
    rank: int | None = None


@dataclass(frozen=True)
class RequirementScoreView:
    """One cell from dbo.rm_requirement_score. Score is 0–10; weight is a percent."""

    score: int
    weight: int


class BidsProjection(Protocol):
    def apply_submitted(
        self, bid_id: str, data: dict, *, tender_name: str, submitted_at: datetime, supplier_name: str = ""
    ) -> None: ...

    def assessment(self, bid_id: str) -> AssessmentView | None: ...

    def for_supplier(self, supplier_id: str) -> list[SupplierBidView]: ...

    def count_for(self, tender_id: str) -> int: ...

    def apply_withdrawn(self, bid_id: str) -> None: ...

    def has_active(self, tender_id: str, supplier_id: str) -> bool: ...

    def clear(self) -> None: ...


class InMemoryBidsProjection:
    def __init__(self) -> None:
        self._assessment: dict[str, AssessmentView] = {}
        self._supplier: dict[str, SupplierBidView] = {}
        self._comparison: dict[str, ComparisonRow] = {}
        self._requirement_scores: dict[str, dict[str, dict[str, RequirementScoreView]]] = {}

    def apply_submitted(
        self, bid_id: str, data: dict, *, tender_name: str, submitted_at: datetime, supplier_name: str = ""
    ) -> None:
        responses = tuple(
            ResponseView(item["requirement_id"], item["answer"]) for item in data["responses"]
        )
        self._assessment[bid_id] = AssessmentView(
            bid_id=bid_id,
            tender_id=data["tender_id"],
            concept=data["concept"],
            responses=responses,
        )
        self._supplier[bid_id] = SupplierBidView(
            bid_id=bid_id,
            tender_id=data["tender_id"],
            supplier_id=data["supplier_id"],
            tender_name=tender_name,
            status=SUBMITTED,
            submitted_at=submitted_at,
        )
        tender_id = data["tender_id"]
        self._comparison[bid_id] = ComparisonRow(
            bid_id=bid_id,
            tender_id=tender_id,
            supplier_id=data["supplier_id"],
            supplier_name=supplier_name or data["supplier_id"],
            status=SUBMITTED,
            submitted_at=submitted_at,
        )

    def assessment(self, bid_id: str) -> AssessmentView | None:
        return self._assessment.get(bid_id)

    def for_supplier(self, supplier_id: str) -> list[SupplierBidView]:
        rows = [row for row in self._supplier.values() if row.supplier_id == supplier_id]
        rows.sort(key=lambda row: (row.submitted_at, row.tender_name))
        return rows

    def count_for(self, tender_id: str) -> int:
        return sum(
            1
            for row in self._supplier.values()
            if row.tender_id == tender_id and row.status == SUBMITTED
        )

    def apply_withdrawn(self, bid_id: str) -> None:
        self._supplier[bid_id] = replace(self._supplier[bid_id], status=WITHDRAWN)
        self._comparison[bid_id] = replace(self._comparison[bid_id], status=WITHDRAWN)

    def apply_assessed(self, bid_id: str, status: str, quality: Decimal | float | None) -> None:
        """Record the terminal quality. The sealed price stays unset."""
        current = self._comparison.get(bid_id)
        if current is None:
            return
        scored = None if quality is None else Decimal(str(quality))
        self._comparison[bid_id] = replace(current, status=status, quality=scored)
        supplier = self._supplier.get(bid_id)
        if supplier is not None:
            self._supplier[bid_id] = replace(supplier, status=status)

    def comparison_for(self, tender_id: str) -> list[ComparisonRow]:
        rows = [row for row in self._comparison.values() if row.tender_id == tender_id]
        rows.sort(key=lambda row: row.submitted_at)
        return rows

    def requirement_scores_for(self, tender_id: str) -> dict[str, dict[str, RequirementScoreView]]:
        stored = self._requirement_scores.get(tender_id, {})
        return {bid_id: dict(scores) for bid_id, scores in stored.items()}

    def remember_requirement_score(
        self,
        tender_id: str,
        bid_id: str,
        requirement_id: str,
        score: int,
        weight: int,
    ) -> None:
        """Test double for dbo.rm_requirement_score. The SQL projection reads the table."""
        self._requirement_scores.setdefault(tender_id, {}).setdefault(bid_id, {})[requirement_id] = (
            RequirementScoreView(score=score, weight=weight)
        )

    def apply_revealed(self, ranking: list[dict], prices: list[dict]) -> None:
        price_of = {item["bid_id"]: int(item["amount"]) for item in prices}
        ranked = {item["bid_id"]: item for item in ranking}
        for bid_id, price in price_of.items():
            current = self._comparison[bid_id]
            if bid_id in ranked:
                item = ranked[bid_id]
                updated = replace(
                    current,
                    status=QUALIFIED,
                    quality=Decimal(str(item["quality"])),
                    price_amount=price,
                    price_score=Decimal(str(item["price_score"])),
                    final_score=Decimal(str(item["final_score"])),
                    rank=int(item["rank"]),
                )
                supplier_status = QUALIFIED
            else:
                updated = replace(
                    current,
                    status=DISQUALIFIED,
                    price_amount=price,
                    quality=None,
                    price_score=None,
                    final_score=None,
                    rank=None,
                )
                supplier_status = DISQUALIFIED
            self._comparison[bid_id] = updated
            supplier = self._supplier.get(bid_id)
            if supplier is not None:
                self._supplier[bid_id] = replace(supplier, status=supplier_status)

    def has_active(self, tender_id: str, supplier_id: str) -> bool:
        return any(
            row.tender_id == tender_id and row.supplier_id == supplier_id and row.status == SUBMITTED
            for row in self._supplier.values()
        )

    def clear(self) -> None:
        self._assessment.clear()
        self._supplier.clear()
        self._comparison.clear()
        self._requirement_scores.clear()


CREATE_RM_BID_FOR_ASSESSMENT = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_bid_for_assessment' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_bid_for_assessment (
        bid_id          VARCHAR(100)  NOT NULL,
        tender_id       VARCHAR(100)  NOT NULL,
        supplier_id     VARCHAR(100)  NOT NULL,
        concept         NVARCHAR(MAX) NOT NULL,
        responses_json  NVARCHAR(MAX) NOT NULL,
        status          VARCHAR(20)   NOT NULL,
        queued_at       DATETIME2     NOT NULL
            CONSTRAINT df_rm_bid_for_assessment_queued_at DEFAULT SYSUTCDATETIME(),
        claimed_at      DATETIME2     NULL,
        CONSTRAINT pk_rm_bid_for_assessment PRIMARY KEY (bid_id),
        CONSTRAINT ck_rm_bid_for_assessment_status
            CHECK (status IN ('pending', 'claimed', 'completed'))
    );
END
"""

CREATE_RM_SUPPLIER_BID = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_supplier_bid' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_supplier_bid (
        bid_id        VARCHAR(100)  NOT NULL,
        tender_id     VARCHAR(100)  NOT NULL,
        supplier_id   VARCHAR(100)  NOT NULL,
        tender_name   NVARCHAR(200) NOT NULL,
        status        VARCHAR(30)   NOT NULL,
        submitted_at  DATETIME2     NOT NULL,
        CONSTRAINT pk_rm_supplier_bid PRIMARY KEY (bid_id)
    );
    CREATE INDEX ix_rm_supplier_bid_supplier ON dbo.rm_supplier_bid (supplier_id, submitted_at);
    CREATE INDEX ix_rm_supplier_bid_tender ON dbo.rm_supplier_bid (tender_id, supplier_id, status);
END
"""

CREATE_RM_DASHBOARD = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_dashboard' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_dashboard (
        tender_id     VARCHAR(100)  NOT NULL,
        organizer_id  VARCHAR(100)  NOT NULL,
        name          NVARCHAR(200) NOT NULL,
        status        VARCHAR(30)   NOT NULL,
        deadline      DATETIME2     NULL,
        bid_count     INT           NOT NULL,
        CONSTRAINT pk_rm_dashboard PRIMARY KEY (tender_id)
    );
END
"""

ADD_DASHBOARD_COLUMNS = """
IF COL_LENGTH('dbo.rm_dashboard', 'organizer_id') IS NULL
    ALTER TABLE dbo.rm_dashboard ADD organizer_id VARCHAR(100) NULL;
IF COL_LENGTH('dbo.rm_dashboard', 'name') IS NULL
    ALTER TABLE dbo.rm_dashboard ADD name NVARCHAR(200) NULL;
IF COL_LENGTH('dbo.rm_dashboard', 'status') IS NULL
    ALTER TABLE dbo.rm_dashboard ADD status VARCHAR(30) NULL;
IF COL_LENGTH('dbo.rm_dashboard', 'deadline') IS NULL
    EXEC(N'ALTER TABLE dbo.rm_dashboard ADD deadline DATETIME2 NULL');
ELSE
    EXEC(N'ALTER TABLE dbo.rm_dashboard ALTER COLUMN deadline DATETIME2 NULL');
IF COL_LENGTH('dbo.rm_dashboard', 'bid_count') IS NULL
    ALTER TABLE dbo.rm_dashboard ADD bid_count INT NOT NULL
        CONSTRAINT df_rm_dashboard_bid_count DEFAULT 0;
"""

CREATE_RM_BID_RANKING = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_bid_ranking' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_bid_ranking (
        bid_id        VARCHAR(100)  NOT NULL,
        tender_id     VARCHAR(100)  NOT NULL,
        supplier_id   VARCHAR(100)  NOT NULL,
        supplier_name NVARCHAR(200) NOT NULL,
        status        VARCHAR(30)   NOT NULL,
        submitted_at  DATETIME2     NOT NULL,
        quality       DECIMAL(6, 1) NULL,
        price_amount  BIGINT        NULL,
        price_score   DECIMAL(6, 1) NULL,
        final_score   DECIMAL(6, 1) NULL,
        rank          INT           NULL,
        CONSTRAINT pk_rm_bid_ranking PRIMARY KEY (bid_id)
    );
    CREATE INDEX ix_rm_bid_ranking_tender ON dbo.rm_bid_ranking (tender_id, submitted_at);
END
"""


class SqlBidsProjection:
    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def apply_submitted(
        self, bid_id: str, data: dict, *, tender_name: str, submitted_at: datetime, supplier_name: str = ""
    ) -> None:
        responses_json = json.dumps(list(data["responses"]), ensure_ascii=False)
        cursor = self._conn.cursor()
        cursor.setinputsizes(
            [None, None, None, (pyodbc.SQL_WVARCHAR, 0, 0), (pyodbc.SQL_WVARCHAR, 0, 0)]
        )
        cursor.execute(
            "INSERT INTO dbo.rm_bid_for_assessment "
            "(bid_id, tender_id, supplier_id, concept, responses_json, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            bid_id,
            data["tender_id"],
            data["supplier_id"],
            data["concept"],
            responses_json,
        )
        cursor.close()
        cursor = self._conn.cursor()
        cursor.setinputsizes([None, None, None, (pyodbc.SQL_WVARCHAR, 200, 0), None, None])
        cursor.execute(
            "INSERT INTO dbo.rm_supplier_bid "
            "(bid_id, tender_id, supplier_id, tender_name, status, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            bid_id,
            data["tender_id"],
            data["supplier_id"],
            tender_name,
            SUBMITTED,
            _sql_datetime(submitted_at),
        )
        cursor.close()
        cursor = self._conn.cursor()
        cursor.setinputsizes([None, None, None, (pyodbc.SQL_WVARCHAR, 200, 0), None, None])
        cursor.execute(
            "INSERT INTO dbo.rm_bid_ranking "
            "(bid_id, tender_id, supplier_id, supplier_name, status, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            bid_id,
            data["tender_id"],
            data["supplier_id"],
            supplier_name or data["supplier_id"],
            SUBMITTED,
            _sql_datetime(submitted_at),
        )
        cursor.close()

    def assessment(self, bid_id: str) -> AssessmentView | None:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT bid_id, tender_id, concept, responses_json "
            "FROM dbo.rm_bid_for_assessment WHERE bid_id = ?",
            bid_id,
        )
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return None
        responses = tuple(
            ResponseView(item["requirement_id"], item["answer"])
            for item in json.loads(row[3])
        )
        return AssessmentView(
            bid_id=str(row[0]),
            tender_id=str(row[1]),
            concept=str(row[2]),
            responses=responses,
        )

    def for_supplier(self, supplier_id: str) -> list[SupplierBidView]:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT bid_id, tender_id, supplier_id, tender_name, status, submitted_at "
            "FROM dbo.rm_supplier_bid WHERE supplier_id = ? ORDER BY submitted_at, tender_name",
            supplier_id,
        )
        rows = [_supplier_row(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def count_for(self, tender_id: str) -> int:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM dbo.rm_supplier_bid WHERE tender_id = ? AND status = ?",
            tender_id,
            SUBMITTED,
        )
        row = cursor.fetchone()
        cursor.close()
        return int(row[0]) if row else 0

    def apply_withdrawn(self, bid_id: str) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE dbo.rm_supplier_bid SET status = ? WHERE bid_id = ?",
            WITHDRAWN,
            bid_id,
        )
        cursor.execute(
            "UPDATE dbo.rm_bid_ranking SET status = ? WHERE bid_id = ?",
            WITHDRAWN,
            bid_id,
        )
        cursor.close()

    def apply_assessed(self, bid_id: str, status: str, quality: Decimal | float | None) -> None:
        """Record the terminal quality. Price columns stay null until reveal."""
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE dbo.rm_bid_ranking SET status = ?, quality = ? WHERE bid_id = ?",
            status,
            None if quality is None else quality,
            bid_id,
        )
        cursor.execute(
            "UPDATE dbo.rm_supplier_bid SET status = ? WHERE bid_id = ?",
            status,
            bid_id,
        )
        cursor.close()

    def has_active(self, tender_id: str, supplier_id: str) -> bool:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM dbo.rm_supplier_bid "
            "WHERE tender_id = ? AND supplier_id = ? AND status = ?",
            tender_id,
            supplier_id,
            SUBMITTED,
        )
        found = cursor.fetchone() is not None
        cursor.close()
        return found

    def clear(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM dbo.rm_bid_for_assessment")
        cursor.execute("DELETE FROM dbo.rm_supplier_bid")
        cursor.execute("DELETE FROM dbo.rm_bid_ranking")
        cursor.close()

    def comparison_for(self, tender_id: str) -> list[ComparisonRow]:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT bid_id, tender_id, supplier_id, supplier_name, status, submitted_at, "
            "quality, price_amount, price_score, final_score, rank "
            "FROM dbo.rm_bid_ranking WHERE tender_id = ? ORDER BY submitted_at",
            tender_id,
        )
        rows = [_comparison_row(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def requirement_scores_for(self, tender_id: str) -> dict[str, dict[str, RequirementScoreView]]:
        """Category cells for one tender. Price is not on this table (NFR-AGT-4)."""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT s.bid_id, s.requirement_id, s.score, s.weight "
            "FROM dbo.rm_requirement_score AS s "
            "INNER JOIN dbo.rm_requirement AS r ON r.requirement_id = s.requirement_id "
            "WHERE r.tender_id = ?",
            tender_id,
        )
        grouped: dict[str, dict[str, RequirementScoreView]] = {}
        for bid_id, requirement_id, score, weight in cursor.fetchall():
            grouped.setdefault(str(bid_id), {})[str(requirement_id)] = RequirementScoreView(
                score=int(score),
                weight=int(weight),
            )
        cursor.close()
        return grouped

    def apply_revealed(self, ranking: list[dict], prices: list[dict]) -> None:
        price_of = {item["bid_id"]: int(item["amount"]) for item in prices}
        ranked = {item["bid_id"]: item for item in ranking}
        cursor = self._conn.cursor()
        for bid_id, price in price_of.items():
            if bid_id in ranked:
                item = ranked[bid_id]
                cursor.execute(
                    "UPDATE dbo.rm_bid_ranking SET status = ?, quality = ?, price_amount = ?, "
                    "price_score = ?, final_score = ?, rank = ? WHERE bid_id = ?",
                    QUALIFIED,
                    item["quality"],
                    price,
                    item["price_score"],
                    item["final_score"],
                    int(item["rank"]),
                    bid_id,
                )
                cursor.execute(
                    "UPDATE dbo.rm_supplier_bid SET status = ? WHERE bid_id = ?",
                    QUALIFIED,
                    bid_id,
                )
            else:
                cursor.execute(
                    "UPDATE dbo.rm_bid_ranking SET status = ?, price_amount = ?, "
                    "quality = NULL, price_score = NULL, final_score = NULL, rank = NULL "
                    "WHERE bid_id = ?",
                    DISQUALIFIED,
                    price,
                    bid_id,
                )
                cursor.execute(
                    "UPDATE dbo.rm_supplier_bid SET status = ? WHERE bid_id = ?",
                    DISQUALIFIED,
                    bid_id,
                )
        cursor.close()


def create_bids_projection(conn: pyodbc.Connection) -> None:
    from app.projections.schema_sync import ensure_read_model_schema

    cursor = conn.cursor()
    cursor.execute(CREATE_RM_BID_FOR_ASSESSMENT)
    cursor.execute(CREATE_RM_SUPPLIER_BID)
    cursor.execute(CREATE_RM_DASHBOARD)
    cursor.execute(ADD_DASHBOARD_COLUMNS)
    cursor.execute(CREATE_RM_BID_RANKING)
    cursor.close()
    ensure_read_model_schema(conn)


def rebuild_bids(store, bids: BidsProjection) -> None:
    """Truncate bid read models and fold BidSubmitted. Price stays in the stream."""
    bids.clear()
    names: dict[str, str] = {}
    pending = None
    pending_prices = None
    for event in store.read_all():
        if event.event_type in (TENDER_CREATED, TENDER_DETAILS_REVISED):
            names[event.stream_id] = event.event_data["name"]
        elif event.event_type == BID_SUBMITTED:
            pending = event
        elif event.event_type == PRICE_OFFER_SEALED:
            if pending is None or pending.stream_id != event.stream_id:
                raise UnknownEventTypeError(event.event_type)
            data = pending.event_data
            bids.apply_submitted(
                pending.stream_id,
                data,
                tender_name=names[data["tender_id"]],
                submitted_at=_parse_timestamp(event.event_data["sealed_at"]),
            )
            pending = None
        elif event.event_type == PRICES_REVEALED:
            pending_prices = event.event_data["prices"]
        elif event.event_type == FINAL_RANKING_CALCULATED:
            if pending_prices is None:
                raise UnknownEventTypeError(event.event_type)
            bids.apply_revealed(event.event_data["ranking"], pending_prices)
            pending_prices = None
        elif event.event_type == BID_WITHDRAWN:
            bids.apply_withdrawn(event.stream_id)
        elif event.event_type == QUALITY_ASSESSMENT_COMPLETED:
            bids.apply_assessed(event.stream_id, QUALIFIED, event.event_data.get("quality_score"))
        elif event.event_type == BID_DISQUALIFIED:
            bids.apply_assessed(event.stream_id, DISQUALIFIED, event.event_data.get("quality_score"))
        elif event.event_type in (REQUIREMENT_SCORED, SCORE_OVERRIDDEN):
            continue
        elif event.event_type in BID_EVENTS:
            raise UnknownEventTypeError(event.event_type)


def _comparison_row(row) -> ComparisonRow:
    return ComparisonRow(
        bid_id=str(row[0]),
        tender_id=str(row[1]),
        supplier_id=str(row[2]),
        supplier_name=str(row[3]),
        status=str(row[4]),
        submitted_at=_aware(row[5]),
        quality=None if row[6] is None else Decimal(str(row[6])),
        price_amount=None if row[7] is None else int(row[7]),
        price_score=None if row[8] is None else Decimal(str(row[8])),
        final_score=None if row[9] is None else Decimal(str(row[9])),
        rank=None if row[10] is None else int(row[10]),
    )


def _supplier_row(row) -> SupplierBidView:
    return SupplierBidView(
        bid_id=str(row[0]),
        tender_id=str(row[1]),
        supplier_id=str(row[2]),
        tender_name=str(row[3]),
        status=str(row[4]),
        submitted_at=_aware(row[5]),
    )


def _parse_timestamp(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sql_datetime(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when
    return when.astimezone(timezone.utc).replace(tzinfo=None)
