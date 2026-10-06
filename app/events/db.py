"""Connection factory for SQL Server on Somee.

pyodbc delegates TLS to the Windows SChannel stack, so the database connection
is not affected by ENV-1. bootstrap.init() is still called here because this is
often the first module a process touches, and truststore must be injected
before any later OpenAI or Tavily client is built.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pyodbc

from app import bootstrap
from app.config import load_settings


def connect() -> pyodbc.Connection:
    bootstrap.init()
    settings = load_settings()
    return pyodbc.connect(settings.db_connection_string, autocommit=False)


@contextmanager
def transaction() -> Iterator[pyodbc.Connection]:
    """Commits on success, rolls back on any exception.

    Event append and read-model projection share one transaction (NFR-CQRS-3),
    so a failed projection cannot leave its event behind.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def available_sql_server_drivers() -> list[str]:
    """Installed ODBC drivers, for diagnosing connection failures."""
    return [d for d in pyodbc.drivers() if "SQL Server" in d]
