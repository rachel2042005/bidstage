"""Bid-receipt message. The price is not a field of the receipt (FR-ENT-6)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BidReceipt:
    to_email: str
    tender_name: str
    bid_id: str
    subject: str
    body: str


def make_receipt(*, to_email: str, tender_name: str, bid_id: str) -> BidReceipt:
    subject = f"הצעתכם התקבלה — {tender_name}"
    body = f"ההצעה למכרז {tender_name} התקבלה.\nמספר הצעה: {bid_id}"
    return BidReceipt(
        to_email=to_email,
        tender_name=tender_name,
        bid_id=bid_id,
        subject=subject,
        body=body,
    )
