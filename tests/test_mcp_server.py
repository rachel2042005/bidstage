"""Own MCP tool get_tender_requirements (T-AG-2, T-AG-3).

Seam: the pure function, against an in-memory TenderRequirementsQuery.
FastMCP transport and SQL projections are outside this file.
"""

from __future__ import annotations

import pytest

from mcp_server.server import TenderNotFound, get_tender_requirements

_TENDER = {
    "name": "טקס פרסי התרבות",
    "type": "culture",
    "event_date": "2026-06-01",
    "region": "תל אביב",
    "alpha": 0.6,
    "price": 900000,
}

_VENUE = {
    "id": "req-venue",
    "type": "VENUE",
    "weight": 40,
    "is_threshold": True,
    "description": "אולם של 2000 מושבים",
    "price": 900000,
}


class InMemoryTenderRequirements:
    """Query double. Missing ids return None."""

    def __init__(self, views: dict[str, dict]) -> None:
        self._views = views

    def fetch(self, tender_id: str) -> dict | None:
        return self._views.get(tender_id)


def test_get_tender_requirements_returns_the_tender_without_price_or_hosts() -> None:
    """T-AG-2: the payload is the spec shape, even when the view carries a price."""
    query = InMemoryTenderRequirements(
        {
            "tender-1": {
                "tender": _TENDER,
                "requirements": [_VENUE],
                "approved_hosts": ["רינה"],
            }
        }
    )

    result = get_tender_requirements("tender-1", query)

    assert result == {
        "tender": {
            "name": "טקס פרסי התרבות",
            "type": "culture",
            "event_date": "2026-06-01",
            "region": "תל אביב",
            "alpha": 0.6,
        },
        "requirements": [
            {
                "id": "req-venue",
                "type": "VENUE",
                "weight": 40,
                "is_threshold": True,
                "description": "אולם של 2000 מושבים",
            }
        ],
    }


_HOST = {
    "id": "req-host",
    "type": "HOST",
    "weight": 20,
    "is_threshold": False,
    "description": "מנחה מהרשימה המאושרת",
}


def test_get_tender_requirements_includes_approved_hosts_for_a_host_requirement() -> None:
    """T-AG-3: the host list is the one the query supplied."""
    query = InMemoryTenderRequirements(
        {
            "tender-1": {
                "tender": _TENDER,
                "requirements": [_VENUE, _HOST],
                "approved_hosts": ["רינה", "יוסי"],
            }
        }
    )

    result = get_tender_requirements("tender-1", query)

    assert result["approved_hosts"] == ["רינה", "יוסי"]
    assert result["requirements"][1]["type"] == "HOST"


class _QueryThatMustNotRun:
    def fetch(self, tender_id: str) -> dict | None:
        raise AssertionError(f"fetch was called with {tender_id!r}")


def test_get_tender_requirements_rejects_a_blank_id_before_lookup() -> None:
    """A blank id is a caller error, so the query never sees it."""
    query = _QueryThatMustNotRun()

    for tender_id in ("", "   "):
        with pytest.raises(ValueError, match="tender_id"):
            get_tender_requirements(tender_id, query)


def test_get_tender_requirements_raises_when_the_tender_is_missing() -> None:
    """An unknown id is a lookup failure, and the message names that id."""
    query = InMemoryTenderRequirements({})

    with pytest.raises(TenderNotFound, match="tender-404"):
        get_tender_requirements("tender-404", query)
