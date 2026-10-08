"""Tender read models: rm_tender_search, rm_tender_detail, rm_requirement.

Projected inside the command that appended the event (NFR-CQRS-3).
SearchTenders and GetTenderDetails read these tables, never dbo.events.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol

import pyodbc

from app.domain.tender import DRAFT, OPEN
from app.domain.user import UnknownEventTypeError
from app.events.event_types import (
    REQUIREMENT_ADDED,
    TENDER_CREATED,
    TENDER_EVENTS,
    TENDER_PUBLISHED,
)

CREATE_RM_TENDER_SEARCH = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_tender_search' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_tender_search (
        tender_id        VARCHAR(100)  NOT NULL,
        organizer_id     VARCHAR(100)  NOT NULL,
        name             NVARCHAR(200) NOT NULL,
        event_type       VARCHAR(20)   NOT NULL,
        event_date       DATE          NOT NULL,
        region           NVARCHAR(100) NOT NULL,
        deadline         DATETIME2     NOT NULL,
        status           VARCHAR(30)   NOT NULL,
        alpha            DECIMAL(3, 2) NOT NULL,
        budget_amount    BIGINT        NOT NULL,
        budget_currency  CHAR(3)       NOT NULL,
        published_at     DATETIME2     NULL,
        search_text      NVARCHAR(MAX) NOT NULL,
        CONSTRAINT pk_rm_tender_search PRIMARY KEY (tender_id),
        CONSTRAINT ck_rm_tender_search_status CHECK (
            status IN ('draft', 'open', 'under_evaluation', 'decided', 'failed', 'cancelled')
        )
    );
    CREATE INDEX ix_rm_tender_search_organizer ON dbo.rm_tender_search (organizer_id);
    CREATE INDEX ix_rm_tender_search_status ON dbo.rm_tender_search (status);
END
"""

CREATE_RM_TENDER_DETAIL = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_tender_detail' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_tender_detail (
        tender_id        VARCHAR(100)  NOT NULL,
        organizer_id     VARCHAR(100)  NOT NULL,
        name             NVARCHAR(200) NOT NULL,
        event_type       VARCHAR(20)   NOT NULL,
        event_date       DATE          NOT NULL,
        region           NVARCHAR(100) NOT NULL,
        deadline         DATETIME2     NOT NULL,
        status           VARCHAR(30)   NOT NULL,
        alpha            DECIMAL(3, 2) NOT NULL,
        budget_amount    BIGINT        NOT NULL,
        budget_currency  CHAR(3)       NOT NULL,
        published_at     DATETIME2     NULL,
        CONSTRAINT pk_rm_tender_detail PRIMARY KEY (tender_id)
    );
END
"""

CREATE_RM_REQUIREMENT = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'rm_requirement' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_requirement (
        requirement_id    VARCHAR(100)  NOT NULL,
        tender_id         VARCHAR(100)  NOT NULL,
        requirement_type  VARCHAR(20)   NOT NULL,
        weight            INT           NOT NULL,
        is_threshold      BIT           NOT NULL,
        description       NVARCHAR(MAX) NOT NULL,
        sort_order        INT           NOT NULL,
        CONSTRAINT pk_rm_requirement PRIMARY KEY (requirement_id)
    );
    CREATE INDEX ix_rm_requirement_tender ON dbo.rm_requirement (tender_id, sort_order);
END
"""

# Tables created before search_text existed still need the column and the
# lookup index. Both statements are idempotent.
ADD_SEARCH_TEXT = """
IF COL_LENGTH('dbo.rm_tender_search', 'search_text') IS NULL
    ALTER TABLE dbo.rm_tender_search
        ADD search_text NVARCHAR(MAX) NOT NULL
            CONSTRAINT df_rm_tender_search_text DEFAULT N'';
"""

CREATE_SEARCH_LOOKUP_INDEX = """
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'ix_rm_tender_search_lookup'
)
    CREATE INDEX ix_rm_tender_search_lookup
        ON dbo.rm_tender_search (status, event_type, event_date);
"""

_SUMMARY_COLUMNS = (
    "tender_id, organizer_id, name, event_type, event_date, region, deadline, "
    "status, alpha, budget_amount, budget_currency, published_at"
)


@dataclass(frozen=True)
class TenderSummary:
    tender_id: str
    organizer_id: str
    name: str
    event_type: str
    event_date: date
    region: str
    deadline: datetime
    status: str
    alpha: Decimal
    budget_amount: int
    budget_currency: str
    published_at: datetime | None = None


@dataclass(frozen=True)
class RequirementRecord:
    requirement_id: str
    tender_id: str
    requirement_type: str
    weight: int
    is_threshold: bool
    description: str


@dataclass(frozen=True)
class TenderSearch:
    """Filters for SearchTenders. Empty fields are unconstrained."""

    organizer_id: str | None = None
    status: str | None = None
    event_type: str | None = None
    region: str | None = None
    event_date_from: date | None = None
    event_date_to: date | None = None
    keyword: str | None = None


class TendersProjection(Protocol):
    def apply_created(self, tender_id: str, data: dict) -> None: ...
    def apply_requirement(self, tender_id: str, data: dict) -> None: ...
    def apply_published(self, tender_id: str, data: dict) -> None: ...
    def get(self, tender_id: str) -> TenderSummary | None: ...
    def requirements_for(self, tender_id: str) -> list[RequirementRecord]: ...
    def search(self, criteria: TenderSearch) -> list[TenderSummary]: ...
    def clear(self) -> None: ...


class InMemoryTendersProjection:
    def __init__(self) -> None:
        self._tenders: dict[str, TenderSummary] = {}
        self._requirements: dict[str, list[RequirementRecord]] = {}
        self._search_text: dict[str, str] = {}

    def apply_created(self, tender_id: str, data: dict) -> None:
        self._tenders[tender_id] = _summary_from_created(tender_id, data)
        self._requirements[tender_id] = []
        self._search_text[tender_id] = initial_search_text(data)

    def apply_requirement(self, tender_id: str, data: dict) -> None:
        if tender_id not in self._tenders:
            raise KeyError(tender_id)
        self._requirements[tender_id].append(_requirement_from_data(tender_id, data))
        self._search_text[tender_id] = (
            self._search_text[tender_id] + "\n" + requirement_search_text(data)
        )

    def apply_published(self, tender_id: str, data: dict) -> None:
        current = self._tenders[tender_id]
        self._tenders[tender_id] = replace(
            current,
            status=OPEN,
            published_at=_parse_timestamp(data["published_at"]),
        )

    def get(self, tender_id: str) -> TenderSummary | None:
        return self._tenders.get(tender_id)

    def requirements_for(self, tender_id: str) -> list[RequirementRecord]:
        return list(self._requirements.get(tender_id, []))

    def search(self, criteria: TenderSearch) -> list[TenderSummary]:
        region_key = criteria.region.casefold().strip() if criteria.region else None
        matched = []
        for tender_id, row in self._tenders.items():
            if criteria.organizer_id is not None and row.organizer_id != criteria.organizer_id:
                continue
            if criteria.status is not None and row.status != criteria.status:
                continue
            if criteria.event_type is not None and row.event_type != criteria.event_type:
                continue
            if region_key is not None and row.region.casefold() != region_key:
                continue
            if criteria.event_date_from is not None and row.event_date < criteria.event_date_from:
                continue
            if criteria.event_date_to is not None and row.event_date > criteria.event_date_to:
                continue
            if not keyword_matches(self._search_text.get(tender_id, ""), criteria.keyword):
                continue
            matched.append(row)
        matched.sort(key=lambda row: (row.event_date, row.name))
        return matched

    def clear(self) -> None:
        self._tenders.clear()
        self._requirements.clear()
        self._search_text.clear()


class SqlTendersProjection:
    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def apply_created(self, tender_id: str, data: dict) -> None:
        summary = _summary_from_created(tender_id, data)
        text = initial_search_text(data)
        _insert_summary(self._conn, "dbo.rm_tender_detail", summary)
        _insert_summary(self._conn, "dbo.rm_tender_search", summary, search_text=text)

    def apply_requirement(self, tender_id: str, data: dict) -> None:
        record = _requirement_from_data(tender_id, data)
        sort_order = self._next_sort_order(tender_id)
        cursor = self._conn.cursor()
        cursor.setinputsizes(
            [None, None, None, None, None, (pyodbc.SQL_WVARCHAR, 0, 0), None]
        )
        cursor.execute(
            "INSERT INTO dbo.rm_requirement "
            "(requirement_id, tender_id, requirement_type, weight, is_threshold, "
            "description, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
            record.requirement_id,
            record.tender_id,
            record.requirement_type,
            record.weight,
            1 if record.is_threshold else 0,
            record.description,
            sort_order,
        )
        cursor.close()
        cursor = self._conn.cursor()
        cursor.setinputsizes([(pyodbc.SQL_WVARCHAR, 0, 0), None])
        cursor.execute(
            "UPDATE dbo.rm_tender_search "
            "SET search_text = CONCAT(search_text, NCHAR(10), ?) WHERE tender_id = ?",
            requirement_search_text(data),
            tender_id,
        )
        cursor.close()

    def apply_published(self, tender_id: str, data: dict) -> None:
        published_at = _sql_datetime(_parse_timestamp(data["published_at"]))
        cursor = self._conn.cursor()
        for table in ("dbo.rm_tender_search", "dbo.rm_tender_detail"):
            cursor.execute(
                f"UPDATE {table} SET status = ?, published_at = ? WHERE tender_id = ?",
                OPEN,
                published_at,
                tender_id,
            )
        cursor.close()

    def get(self, tender_id: str) -> TenderSummary | None:
        return _fetch_one(
            self._conn,
            f"SELECT {_SUMMARY_COLUMNS} FROM dbo.rm_tender_detail WHERE tender_id = ?",
            tender_id,
        )

    def requirements_for(self, tender_id: str) -> list[RequirementRecord]:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT requirement_id, tender_id, requirement_type, weight, "
            "is_threshold, description FROM dbo.rm_requirement "
            "WHERE tender_id = ? ORDER BY sort_order",
            tender_id,
        )
        rows = [
            RequirementRecord(
                requirement_id=str(row[0]),
                tender_id=str(row[1]),
                requirement_type=str(row[2]),
                weight=int(row[3]),
                is_threshold=bool(row[4]),
                description=str(row[5]),
            )
            for row in cursor.fetchall()
        ]
        cursor.close()
        return rows

    def search(self, criteria: TenderSearch) -> list[TenderSummary]:
        sql, params = compile_search_sql(criteria)
        cursor = self._conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        rows = [_row_to_summary(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def clear(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM dbo.rm_requirement")
        cursor.execute("DELETE FROM dbo.rm_tender_search")
        cursor.execute("DELETE FROM dbo.rm_tender_detail")
        cursor.close()

    def _next_sort_order(self, tender_id: str) -> int:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM dbo.rm_requirement WHERE tender_id = ?",
            tender_id,
        )
        value = int(cursor.fetchone()[0]) + 1
        cursor.close()
        return value


def create_tenders_projection(conn: pyodbc.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(CREATE_RM_TENDER_SEARCH)
    cursor.execute(CREATE_RM_TENDER_DETAIL)
    cursor.execute(CREATE_RM_REQUIREMENT)
    cursor.execute(ADD_SEARCH_TEXT)
    cursor.execute(CREATE_SEARCH_LOOKUP_INDEX)
    cursor.close()


def project_tender_event(tenders: TendersProjection, event) -> None:
    if event.event_type == TENDER_CREATED:
        tenders.apply_created(event.stream_id, event.event_data)
    elif event.event_type == REQUIREMENT_ADDED:
        tenders.apply_requirement(event.stream_id, event.event_data)
    elif event.event_type == TENDER_PUBLISHED:
        tenders.apply_published(event.stream_id, event.event_data)
    elif event.event_type in TENDER_EVENTS:
        raise UnknownEventTypeError(event.event_type)


def rebuild_tenders(store, tenders: TendersProjection) -> None:
    """Truncate the tender read models and fold the stream in global order."""
    tenders.clear()
    for event in store.read_all():
        if event.event_type in TENDER_EVENTS:
            project_tender_event(tenders, event)


def _summary_from_created(tender_id: str, data: dict) -> TenderSummary:
    budget = data["estimated_budget"]
    return TenderSummary(
        tender_id=tender_id,
        organizer_id=data["organizer_id"],
        name=data["name"],
        event_type=data["event_type"],
        event_date=date.fromisoformat(data["event_date"]),
        region=data["region"],
        deadline=_parse_timestamp(data["deadline"]),
        status=DRAFT,
        alpha=Decimal(data["alpha"]),
        budget_amount=int(budget["amount"]),
        budget_currency=str(budget["currency"]),
    )


def _requirement_from_data(tender_id: str, data: dict) -> RequirementRecord:
    return RequirementRecord(
        requirement_id=data["requirement_id"],
        tender_id=tender_id,
        requirement_type=data["type"],
        weight=int(data["weight"]),
        is_threshold=bool(data["is_threshold"]),
        description=data["description"],
    )


def _insert_summary(
    conn: pyodbc.Connection,
    table: str,
    summary: TenderSummary,
    search_text: str | None = None,
) -> None:
    columns = _SUMMARY_COLUMNS
    values = "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
    params: list = [
        summary.tender_id,
        summary.organizer_id,
        summary.name,
        summary.event_type,
        summary.event_date,
        summary.region,
        _sql_datetime(summary.deadline),
        summary.status,
        summary.alpha,
        summary.budget_amount,
        summary.budget_currency,
        _sql_datetime(summary.published_at),
    ]
    sizes: list = [
        None,
        None,
        (pyodbc.SQL_WVARCHAR, 200, 0),
        None,
        None,
        (pyodbc.SQL_WVARCHAR, 100, 0),
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    if search_text is not None:
        columns = f"{columns}, search_text"
        values = f"{values}, ?"
        params.append(search_text)
        sizes.append((pyodbc.SQL_WVARCHAR, 0, 0))
    cursor = conn.cursor()
    cursor.setinputsizes(sizes)
    cursor.execute(f"INSERT INTO {table} ({columns}) VALUES ({values})", params)
    cursor.close()


def keyword_matches(document: str, keyword: str | None) -> bool:
    """Every whitespace-separated token must appear in the projected search text."""
    tokens = [part.casefold() for part in (keyword or "").split() if part]
    folded = document.casefold()
    return all(token in folded for token in tokens)


def initial_search_text(data: dict) -> str:
    return f"{data['name']}\n{data['region']}".casefold()


def requirement_search_text(data: dict) -> str:
    return f"{data['type']}\n{data['description']}".casefold()


def compile_search_sql(criteria: TenderSearch) -> tuple[str, list]:
    """Parameterized lookup. Values never get interpolated into the SQL text."""
    clauses: list[str] = []
    params: list = []
    if criteria.organizer_id is not None:
        clauses.append("organizer_id = ?")
        params.append(criteria.organizer_id)
    if criteria.status is not None:
        clauses.append("status = ?")
        params.append(criteria.status)
    if criteria.event_type is not None:
        clauses.append("event_type = ?")
        params.append(criteria.event_type)
    if criteria.region is not None and criteria.region.strip():
        clauses.append("LOWER(region) = LOWER(?)")
        params.append(criteria.region.strip())
    if criteria.event_date_from is not None:
        clauses.append("event_date >= ?")
        params.append(criteria.event_date_from)
    if criteria.event_date_to is not None:
        clauses.append("event_date <= ?")
        params.append(criteria.event_date_to)
    for token in (criteria.keyword or "").split():
        if not token:
            continue
        clauses.append("search_text LIKE ?")
        params.append(_like_contains(token.casefold()))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT {_SUMMARY_COLUMNS} FROM dbo.rm_tender_search"
        f"{where} ORDER BY event_date, name"
    )
    return sql, params


def _like_contains(token: str) -> str:
    safe = token.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
    return f"%{safe}%"


def _fetch_one(conn: pyodbc.Connection, sql: str, tender_id: str) -> TenderSummary | None:
    cursor = conn.cursor()
    cursor.execute(sql, tender_id)
    row = cursor.fetchone()
    cursor.close()
    return _row_to_summary(row) if row else None


def _row_to_summary(row) -> TenderSummary:
    event_date = row[4]
    if isinstance(event_date, datetime):
        event_date = event_date.date()
    return TenderSummary(
        tender_id=str(row[0]),
        organizer_id=str(row[1]),
        name=str(row[2]),
        event_type=str(row[3]),
        event_date=event_date,
        region=str(row[5]),
        deadline=_aware(row[6]),
        status=str(row[7]),
        alpha=Decimal(row[8]),
        budget_amount=int(row[9]),
        budget_currency=str(row[10]).strip(),
        published_at=_aware(row[11]),
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _aware(parsed)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sql_datetime(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    if when.tzinfo is None:
        return when
    return when.astimezone(timezone.utc).replace(tzinfo=None)
