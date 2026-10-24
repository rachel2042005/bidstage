"""Winner and non-winner messages. Neither one carries a price (FR-NOT-3)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AwardNotice:
    to_email: str
    subject: str
    body: str
    won: bool


def make_award_notice(*, to_email: str, tender_name: str, won: bool) -> AwardNotice:
    if won:
        subject = f"זכיתם במכרז — {tender_name}"
        body = f"הצעתכם למכרז {tender_name} נבחרה."
    else:
        subject = f"המכרז הוכרע — {tender_name}"
        body = f"הצעתכם למכרז {tender_name} לא נבחרה."
    return AwardNotice(to_email=to_email, subject=subject, body=body, won=won)
