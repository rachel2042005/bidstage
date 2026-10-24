"""Gmail MCP transport for bid receipts (NFR-MCP-2).

Used by the web process only, and only after the bid transaction commits.
A failure is the caller's to swallow (FR-NOT-4). When GMAIL_MCP_URL is unset
the factory returns a notifier that does not send.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol

from app.notifications.receipt import BidReceipt


class BidNotifier(Protocol):
    def bid_received(self, receipt: BidReceipt) -> None: ...

    def send(self, to: str, subject: str, body: str) -> None: ...


class NullBidNotifier:
    def bid_received(self, receipt: BidReceipt) -> None:
        return None

    def send(self, to: str, subject: str, body: str) -> None:
        return None


class BidNotificationError(RuntimeError):
    pass


class GmailMcpNotifier:
    """POST a Gmail MCP `send_email` tool call. The body is the receipt only."""

    def __init__(self, url: str) -> None:
        self._url = url

    def bid_received(self, receipt: BidReceipt) -> None:
        self.send(receipt.to_email, receipt.subject, receipt.body)

    def send(self, to: str, subject: str, body: str) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "send_email",
                "arguments": {"to": to, "subject": subject, "body": body},
            },
        }
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = getattr(response, "status", 200)
        except OSError as exc:
            raise BidNotificationError("Gmail MCP request failed") from exc
        if status >= 400:
            raise BidNotificationError(f"Gmail MCP returned {status}")


def notifier_from_environment() -> BidNotifier:
    url = os.getenv("GMAIL_MCP_URL", "").strip()
    if not url:
        return NullBidNotifier()
    return GmailMcpNotifier(url)
