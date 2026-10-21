"""Fill in read-model columns that an older SQL Server database never received.

CREATE TABLE is skipped once the table exists, so a column added later is
absent, and a leftover NOT NULL column with no default (for example
rm_requirement.position) rejects an INSERT that does not name it.
"""

from __future__ import annotations

import re

import pyodbc

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Non-key columns the projections write. NOT NULL definitions carry a default
# so adding the column succeeds on a table that already has rows.
_ADD_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("rm_tender_search", "organizer_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_tender_search", "name", "NVARCHAR(200) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_tender_search", "event_type", "VARCHAR(20) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_tender_search", "event_date", "DATE NULL"),
    ("rm_tender_search", "region", "NVARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_tender_search", "deadline", "DATETIME2 NULL"),
    ("rm_tender_search", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'draft'"),
    ("rm_tender_search", "alpha", "DECIMAL(3, 2) NOT NULL CONSTRAINT {c} DEFAULT 0.60"),
    ("rm_tender_search", "budget_amount", "BIGINT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_tender_search", "budget_currency", "CHAR(3) NOT NULL CONSTRAINT {c} DEFAULT 'ILS'"),
    ("rm_tender_search", "published_at", "DATETIME2 NULL"),
    ("rm_tender_search", "search_text", "NVARCHAR(MAX) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_tender_detail", "organizer_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_tender_detail", "name", "NVARCHAR(200) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_tender_detail", "event_type", "VARCHAR(20) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_tender_detail", "event_date", "DATE NULL"),
    ("rm_tender_detail", "region", "NVARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_tender_detail", "deadline", "DATETIME2 NULL"),
    ("rm_tender_detail", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'draft'"),
    ("rm_tender_detail", "alpha", "DECIMAL(3, 2) NOT NULL CONSTRAINT {c} DEFAULT 0.60"),
    ("rm_tender_detail", "budget_amount", "BIGINT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_tender_detail", "budget_currency", "CHAR(3) NOT NULL CONSTRAINT {c} DEFAULT 'ILS'"),
    ("rm_tender_detail", "published_at", "DATETIME2 NULL"),
    ("rm_requirement", "tender_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_requirement", "requirement_id", "VARCHAR(100) NULL"),
    ("rm_requirement", "requirement_type", "VARCHAR(20) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_requirement", "weight", "INT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_requirement", "is_threshold", "BIT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_requirement", "description", "NVARCHAR(MAX) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_requirement", "sort_order", "INT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_requirement", "position", "INT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_requirement", "type", "VARCHAR(20) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_dashboard", "organizer_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_dashboard", "name", "NVARCHAR(200) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_dashboard", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'draft'"),
    ("rm_dashboard", "deadline", "DATETIME2 NULL"),
    ("rm_dashboard", "bid_count", "INT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_user", "email", "NVARCHAR(320) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_user", "display_name", "NVARCHAR(200) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_user", "role", "VARCHAR(20) NOT NULL CONSTRAINT {c} DEFAULT 'organizer'"),
    ("rm_user", "password_hash", "NVARCHAR(255) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_user", "registered_at", "DATETIME2 NOT NULL CONSTRAINT {c} DEFAULT SYSUTCDATETIME()"),
    ("rm_user", "last_login_at", "DATETIME2 NULL"),
    ("rm_bid_for_assessment", "tender_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_bid_for_assessment", "supplier_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_bid_for_assessment", "concept", "NVARCHAR(MAX) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_bid_for_assessment", "responses_json", "NVARCHAR(MAX) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_bid_for_assessment", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'pending'"),
    ("rm_bid_for_assessment", "queued_at", "DATETIME2 NOT NULL CONSTRAINT {c} DEFAULT SYSUTCDATETIME()"),
    ("rm_bid_for_assessment", "claimed_at", "DATETIME2 NULL"),
    ("rm_supplier_bid", "tender_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_supplier_bid", "supplier_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_supplier_bid", "tender_name", "NVARCHAR(200) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_supplier_bid", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'submitted'"),
    ("rm_supplier_bid", "submitted_at", "DATETIME2 NOT NULL CONSTRAINT {c} DEFAULT SYSUTCDATETIME()"),
    ("rm_bid_ranking", "tender_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_bid_ranking", "supplier_id", "VARCHAR(100) NOT NULL CONSTRAINT {c} DEFAULT ''"),
    ("rm_bid_ranking", "supplier_name", "NVARCHAR(200) NOT NULL CONSTRAINT {c} DEFAULT N''"),
    ("rm_bid_ranking", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'submitted'"),
    ("rm_bid_ranking", "submitted_at", "DATETIME2 NOT NULL CONSTRAINT {c} DEFAULT SYSUTCDATETIME()"),
    ("rm_bid_ranking", "quality", "FLOAT NULL"),
    ("rm_bid_ranking", "price_amount", "BIGINT NULL"),
    ("rm_bid_ranking", "price_score", "FLOAT NULL"),
    ("rm_bid_ranking", "final_score", "FLOAT NULL"),
    ("rm_bid_ranking", "rank", "INT NULL"),
    ("rm_bid_assessment", "tender_id", "VARCHAR(100) NULL"),
    ("rm_bid_assessment", "status", "VARCHAR(30) NOT NULL CONSTRAINT {c} DEFAULT 'pending'"),
    ("rm_bid_assessment", "quality_score", "FLOAT NULL"),
    ("rm_bid_assessment", "reason", "NVARCHAR(MAX) NULL"),
    ("rm_bid_assessment", "failed_requirement_id", "VARCHAR(100) NULL"),
    ("rm_requirement_score", "score", "INT NOT NULL CONSTRAINT {c} DEFAULT 0"),
    ("rm_requirement_score", "weight", "INT NOT NULL CONSTRAINT {c} DEFAULT 0"),
)

# Columns the commands assign NULL, or that must stay empty until a later event
# (a bid's price is not known when the row is inserted).
_NULLABLE: tuple[tuple[str, str, str], ...] = (
    ("rm_tender_search", "event_date", "DATE NULL"),
    ("rm_tender_search", "deadline", "DATETIME2 NULL"),
    ("rm_tender_search", "published_at", "DATETIME2 NULL"),
    ("rm_tender_detail", "event_date", "DATE NULL"),
    ("rm_tender_detail", "deadline", "DATETIME2 NULL"),
    ("rm_tender_detail", "published_at", "DATETIME2 NULL"),
    ("rm_dashboard", "deadline", "DATETIME2 NULL"),
    ("rm_user", "last_login_at", "DATETIME2 NULL"),
    ("rm_bid_for_assessment", "claimed_at", "DATETIME2 NULL"),
    ("rm_bid_ranking", "quality", "FLOAT NULL"),
    ("rm_bid_ranking", "price_amount", "BIGINT NULL"),
    ("rm_bid_ranking", "price_score", "FLOAT NULL"),
    ("rm_bid_ranking", "final_score", "FLOAT NULL"),
    ("rm_bid_ranking", "rank", "INT NULL"),
    ("rm_bid_assessment", "tender_id", "VARCHAR(100) NULL"),
    ("rm_bid_assessment", "quality_score", "FLOAT NULL"),
    ("rm_bid_assessment", "reason", "NVARCHAR(MAX) NULL"),
    ("rm_bid_assessment", "failed_requirement_id", "VARCHAR(100) NULL"),
)

# Defaults that have to satisfy a CHECK, or that the domain treats as the
# value of an omitted column. Anything else is chosen from the SQL type.
_DEFAULT_OVERRIDES = {
    ("rm_bid_for_assessment", "status"): "'pending'",
    ("rm_bid_assessment", "status"): "'pending'",
    ("rm_tender_search", "status"): "'draft'",
    ("rm_tender_detail", "status"): "'draft'",
    ("rm_dashboard", "status"): "'draft'",
    ("rm_supplier_bid", "status"): "'submitted'",
    ("rm_bid_ranking", "status"): "'submitted'",
    ("rm_user", "role"): "'organizer'",
    ("rm_tender_search", "alpha"): "0.60",
    ("rm_tender_detail", "alpha"): "0.60",
    ("rm_tender_search", "budget_currency"): "'ILS'",
    ("rm_tender_detail", "budget_currency"): "'ILS'",
    ("rm_requirement", "position"): "0",
    ("rm_requirement", "sort_order"): "0",
    ("rm_requirement", "type"): "''",
    ("rm_dashboard", "bid_count"): "0",
}

_MISSING_DEFAULTS = """
SELECT t.name, c.name, ty.name
FROM sys.tables t
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints dc
    ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
WHERE t.schema_id = SCHEMA_ID('dbo')
  AND t.name LIKE 'rm[_]%'
  AND c.is_nullable = 0
  AND c.is_identity = 0
  AND dc.object_id IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM sys.indexes i
      JOIN sys.index_columns ic
          ON ic.object_id = i.object_id AND ic.index_id = i.index_id
      WHERE i.object_id = c.object_id
        AND ic.column_id = c.column_id
        AND (i.is_primary_key = 1 OR i.is_unique = 1)
  )
ORDER BY t.name, c.column_id
"""

_synced_connections: set[int] = set()
_synced_process = False


def ensure_read_model_schema(conn: pyodbc.Connection, *, force: bool = False) -> None:
    """Idempotent. Safe on an empty database and on a database created earlier."""
    global _synced_process
    if _synced_process and not force:
        return
    key = id(conn)
    if not force and key in _synced_connections:
        return
    cursor = conn.cursor()
    cursor.execute(_add_missing_sql())
    cursor.execute(_relax_nulls_sql())
    cursor.execute(_MISSING_DEFAULTS)
    pending = [(str(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()]
    defaults = _default_sql(pending)
    if defaults:
        cursor.execute(defaults)
    cursor.close()
    _synced_connections.add(key)
    _synced_process = True


def _exec(statement: str) -> str:
    """Defer compilation so a missing table or column is not bound up front."""
    return "EXEC(N'" + statement.replace("'", "''") + "')"


def _add_missing_sql() -> str:
    lines = []
    for table, column, definition in _ADD_COLUMNS:
        _check(table)
        _check(column)
        ddl = definition.format(c=f"df_{table}_{column}")
        lines.append(
            f"IF OBJECT_ID('dbo.{table}', 'U') IS NOT NULL "
            f"AND COL_LENGTH('dbo.{table}', '{column}') IS NULL "
            + _exec(f"ALTER TABLE dbo.{table} ADD {_q(column)} {ddl}")
            + ";"
        )
    return "\n".join(lines)


def _relax_nulls_sql() -> str:
    lines = []
    for table, column, ddl in _NULLABLE:
        _check(table)
        _check(column)
        lines.append(
            f"IF OBJECT_ID('dbo.{table}', 'U') IS NOT NULL "
            f"AND COL_LENGTH('dbo.{table}', '{column}') IS NOT NULL "
            f"AND COLUMNPROPERTY(OBJECT_ID('dbo.{table}'), '{column}', 'AllowsNull') = 0 "
            f"EXEC(N'ALTER TABLE dbo.{table} ALTER COLUMN {_q(column)} {ddl}');"
        )
    return "\n".join(lines)


def _default_sql(columns: list[tuple[str, str, str]]) -> str:
    lines = []
    for table, column, type_name in columns:
        _check(table)
        _check(column)
        expr = _default_expr(table, column, type_name)
        if expr is None:
            continue
        constraint = f"df_{table}_{column}"
        lines.append(
            f"IF OBJECT_ID('dbo.{table}', 'U') IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = '{constraint}') "
            + _exec(
                f"ALTER TABLE dbo.{table} ADD CONSTRAINT {constraint} "
                f"DEFAULT {expr} FOR {_q(column)}"
            )
            + ";"
        )
    return "\n".join(lines)


def _default_expr(table: str, column: str, type_name: str) -> str | None:
    override = _DEFAULT_OVERRIDES.get((table, column))
    if override is not None:
        return override
    kind = type_name.lower()
    if kind in {
        "bit", "int", "smallint", "tinyint", "bigint",
        "decimal", "numeric", "float", "real", "money", "smallmoney",
    }:
        return "0"
    if kind in {"nchar", "nvarchar", "ntext"}:
        return "N''"
    if kind in {"char", "varchar", "text"}:
        return "''"
    if kind == "date":
        return "'19000101'"
    if kind in {"datetime", "datetime2", "smalldatetime", "datetimeoffset"}:
        return "SYSUTCDATETIME()"
    if kind == "time":
        return "'00:00:00'"
    return None


def _check(name: str) -> None:
    if not _IDENT.match(name):
        raise ValueError(name)


def _q(name: str) -> str:
    _check(name)
    return f"[{name}]"
