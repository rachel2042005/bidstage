"""Display helpers for Hebrew RTL templates. Timestamps render in Israel local time."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

ISRAEL = ZoneInfo("Asia/Jerusalem")


def format_shekels(amount: int) -> str:
    shekels = Decimal(amount) / Decimal(100)
    return f"{shekels:,.2f}"


def format_event_day(value: date | datetime) -> str:
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d.%m.%Y")


def format_local_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ISRAEL).strftime("%d.%m.%Y %H:%M")


def format_alpha(alpha: Decimal) -> str:
    percent = (Decimal(alpha) * Decimal(100)).quantize(Decimal("1"))
    return f"{percent}%"


def format_price_share(alpha: Decimal) -> str:
    percent = ((Decimal(1) - Decimal(alpha)) * Decimal(100)).quantize(Decimal("1"))
    return f"{percent}%"
