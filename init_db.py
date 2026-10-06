"""Creates the append-only `events` table if it does not already exist.

Run with: python init_db.py

Idempotent, so it is safe to re-run — which matters because Somee deletes an
idle free database after 30 days (ENV-2) and it then has to be recreated.

On failure it prints evidence at each boundary (config, driver, connection)
rather than a bare stack trace, per the systematic-debugging methodology.
"""

from __future__ import annotations

import sys

from app import bootstrap

bootstrap.init()

import pyodbc  # noqa: E402

from app.config import ConfigError, load_settings  # noqa: E402
from app.events.db import available_sql_server_drivers, transaction  # noqa: E402
from app.events.schema import create_schema, describe_columns, table_exists  # noqa: E402

_ROW_COUNT = "SELECT COUNT(*) FROM dbo.events"


def _report_environment() -> None:
    drivers = available_sql_server_drivers()
    print(f"ODBC SQL Server drivers installed: {drivers or 'NONE'}")
    if not drivers:
        print(
            "  No SQL Server ODBC driver found. Install 'ODBC Driver 17 for "
            "SQL Server' or 'ODBC Driver 18 for SQL Server'."
        )


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    if not settings.db_is_configured:
        print("SOMEE_DB_CONNECTION_STRING still holds the .env.example placeholder.")
        print("Fill in your Somee server, database, user and password in .env.")
        _report_environment()
        return 2

    try:
        with transaction() as conn:
            existed = table_exists(conn)
            create_schema(conn)

            cursor = conn.cursor()
            cursor.execute(_ROW_COUNT)
            rows = int(cursor.fetchone()[0])
            cursor.close()

            columns = describe_columns(conn)
    except pyodbc.Error as exc:
        sqlstate = exc.args[0] if exc.args else "?"
        print(f"Database error (SQLSTATE {sqlstate}): {exc}")
        _report_environment()
        if sqlstate == "08001":
            print(
                "  08001 is a connection failure: wrong server name, blocked "
                "port 1433, or the database was removed for inactivity."
            )
        elif sqlstate == "28000":
            print("  28000 is a login failure: check Uid and Pwd.")
        elif sqlstate == "IM002":
            print("  IM002 means the named driver is not installed.")
        return 1

    print("events table already present." if existed else "events table created.")
    print(f"Events stored: {rows}")
    print("\nColumns:")
    for name, type_name, max_length, nullable in columns:
        length = "max" if max_length == -1 else max_length
        null = "NULL" if nullable else "NOT NULL"
        print(f"  {name:<12} {type_name:<16} len={length:<5} {null}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
