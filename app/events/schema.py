"""DDL for the append-only event store. See specs/02-architecture.md §5.

Columns are the seven specified for Phase 2 — event_id, stream_id, event_type,
event_data, metadata, created_at, version — plus global_seq.

global_seq is not optional: specs/03-domain-events.md §4 requires read models to
be rebuilt by replaying every stream in a total order. version only orders
*within* a stream, and created_at ties when two events share a timestamp, so an
identity column is the only unambiguous global append order.
"""

from __future__ import annotations

import pyodbc

CREATE_EVENTS_TABLE = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'events' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.events (
        global_seq  BIGINT IDENTITY(1,1) NOT NULL,
        event_id    UNIQUEIDENTIFIER     NOT NULL,
        stream_id   VARCHAR(100)         NOT NULL,
        version     INT                  NOT NULL,
        event_type  VARCHAR(100)         NOT NULL,
        event_data  NVARCHAR(MAX)        NOT NULL,
        metadata    NVARCHAR(MAX)        NULL,
        created_at  DATETIME2            NOT NULL
            CONSTRAINT df_events_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT pk_events PRIMARY KEY CLUSTERED (global_seq),
        CONSTRAINT uq_events_event_id UNIQUE (event_id),
        CONSTRAINT uq_events_stream_version UNIQUE (stream_id, version),
        CONSTRAINT ck_events_version_positive CHECK (version > 0)
    );
END
"""

CREATE_EVENT_TYPE_INDEX = """
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_events_event_type')
BEGIN
    CREATE INDEX ix_events_event_type ON dbo.events (event_type);
END
"""

TABLE_EXISTS = (
    "SELECT COUNT(*) FROM sys.tables "
    "WHERE name = 'events' AND schema_id = SCHEMA_ID('dbo')"
)

COLUMN_LIST = """
SELECT c.name, t.name AS type_name, c.max_length, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('dbo.events')
ORDER BY c.column_id
"""


def create_schema(conn: pyodbc.Connection) -> None:
    """Idempotent. Safe to call on every startup."""
    cursor = conn.cursor()
    cursor.execute(CREATE_EVENTS_TABLE)
    cursor.execute(CREATE_EVENT_TYPE_INDEX)
    cursor.close()


def table_exists(conn: pyodbc.Connection) -> bool:
    cursor = conn.cursor()
    cursor.execute(TABLE_EXISTS)
    exists = int(cursor.fetchone()[0]) > 0
    cursor.close()
    return exists


def describe_columns(conn: pyodbc.Connection) -> list[tuple[str, str, int, bool]]:
    cursor = conn.cursor()
    cursor.execute(COLUMN_LIST)
    rows = [(r[0], r[1], int(r[2]), bool(r[3])) for r in cursor.fetchall()]
    cursor.close()
    return rows
