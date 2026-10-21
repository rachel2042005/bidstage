"""rm_user — lookup by email/id for login and authorization (FR-AUTH-3).

Projected inside the command transaction (NFR-CQRS-3). Login never reads
dbo.events (NFR-CQRS-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import pyodbc

from app.domain.user import EmailTakenError, normalize_email, ROLES

CREATE_RM_USER = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'rm_user' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.rm_user (
        user_id       VARCHAR(100)  NOT NULL,
        email         NVARCHAR(320) NOT NULL,
        display_name  NVARCHAR(200) NOT NULL,
        role          VARCHAR(20)   NOT NULL,
        password_hash NVARCHAR(255) NOT NULL,
        registered_at DATETIME2     NOT NULL,
        last_login_at DATETIME2     NULL,
        CONSTRAINT pk_rm_user PRIMARY KEY (user_id),
        CONSTRAINT uq_rm_user_email UNIQUE (email),
        CONSTRAINT ck_rm_user_role CHECK (role IN ('organizer', 'supplier'))
    );
END
"""


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    email: str
    display_name: str
    role: str
    password_hash: str
    registered_at: datetime | None = None
    last_login_at: datetime | None = None

    def __repr__(self) -> str:
        return (
            f"UserRecord(user_id={self.user_id!r}, email={self.email!r}, "
            f"role={self.role!r}, password_hash=***)"
        )


class UsersProjection(Protocol):
    def get_by_email(self, email: str) -> UserRecord | None: ...
    def get_by_id(self, user_id: str) -> UserRecord | None: ...
    def insert(self, record: UserRecord) -> None: ...
    def set_last_login_at(self, user_id: str, when: datetime) -> None: ...


class InMemoryUsersProjection:
    def __init__(self) -> None:
        self._by_id: dict[str, UserRecord] = {}
        self._by_email: dict[str, str] = {}

    def get_by_email(self, email: str) -> UserRecord | None:
        user_id = self._by_email.get(normalize_email(email))
        return self._by_id.get(user_id) if user_id else None

    def get_by_id(self, user_id: str) -> UserRecord | None:
        return self._by_id.get(user_id)

    def insert(self, record: UserRecord) -> None:
        email = normalize_email(record.email)
        if email in self._by_email:
            raise EmailTakenError(email)
        stored = UserRecord(
            user_id=record.user_id,
            email=email,
            display_name=record.display_name,
            role=record.role,
            password_hash=record.password_hash,
            registered_at=record.registered_at,
            last_login_at=record.last_login_at,
        )
        self._by_id[record.user_id] = stored
        self._by_email[email] = record.user_id

    def set_last_login_at(self, user_id: str, when: datetime) -> None:
        current = self._by_id[user_id]
        self._by_id[user_id] = UserRecord(
            user_id=current.user_id,
            email=current.email,
            display_name=current.display_name,
            role=current.role,
            password_hash=current.password_hash,
            registered_at=current.registered_at,
            last_login_at=when,
        )


class SqlUsersProjection:
    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def get_by_email(self, email: str) -> UserRecord | None:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT user_id, email, display_name, role, password_hash, "
            "registered_at, last_login_at FROM dbo.rm_user WHERE email = ?",
            normalize_email(email),
        )
        row = cursor.fetchone()
        cursor.close()
        return _row_to_record(row) if row else None

    def get_by_id(self, user_id: str) -> UserRecord | None:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT user_id, email, display_name, role, password_hash, "
            "registered_at, last_login_at FROM dbo.rm_user WHERE user_id = ?",
            user_id,
        )
        row = cursor.fetchone()
        cursor.close()
        return _row_to_record(row) if row else None

    def insert(self, record: UserRecord) -> None:
        if record.role not in ROLES:
            raise ValueError(record.role)
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO dbo.rm_user "
                "(user_id, email, display_name, role, password_hash, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                record.user_id,
                normalize_email(record.email),
                record.display_name,
                record.role,
                record.password_hash,
                _sql_datetime(record.registered_at),
            )
        except pyodbc.IntegrityError as exc:
            raise EmailTakenError(record.email) from exc
        finally:
            cursor.close()

    def set_last_login_at(self, user_id: str, when: datetime) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE dbo.rm_user SET last_login_at = ? WHERE user_id = ?",
            _sql_datetime(when),
            user_id,
        )
        cursor.close()


def _sql_datetime(when: datetime | None) -> datetime | None:
    """DATETIME2 has no offset. Persist UTC as a naive timestamp."""
    if when is None or when.tzinfo is None:
        return when
    return when.astimezone(timezone.utc).replace(tzinfo=None)


def create_users_projection(conn: pyodbc.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(CREATE_RM_USER)
    cursor.close()


def _row_to_record(row: pyodbc.Row) -> UserRecord:
    return UserRecord(
        user_id=str(row[0]),
        email=str(row[1]),
        display_name=str(row[2]),
        role=str(row[3]),
        password_hash=str(row[4]),
        registered_at=row[5],
        last_login_at=row[6],
    )
