"""Own MCP tools (`T-AG-2`, `T-AG-3`, `T-AG-5`, `T-AG-6`).

Seam: the pure functions, against in-memory query and command doubles.
FastMCP transport and SQL stay outside this file.
"""

from __future__ import annotations

import pytest

from app.commands.score_requirement import ScoreRequirement, ScoringRejected
from mcp_server.server import TenderNotFound, get_tender_requirements, submit_requirement_score

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


class RecordingScoreHandler:
    """Remembers the command and returns a fixed stream version."""

    def __init__(self) -> None:
        self.command: ScoreRequirement | None = None

    def handle(self, command: ScoreRequirement) -> dict:
        self.command = command
        return {"accepted": True, "event_version": 3}


def test_submit_requirement_score_dispatches_the_recorded_score() -> None:
    """The handler receives the §7 command, including scored_by agent."""
    handler = RecordingScoreHandler()

    result = submit_requirement_score(
        "bid-1",
        "req-venue",
        8,
        "The hall seats 2,000 and the plan matches the regulations.",
        ["https://example.com/hall"],
        "1",
        handler,
    )

    assert result == {"accepted": True, "event_version": 3}
    assert handler.command == ScoreRequirement(
        bid_id="bid-1",
        requirement_id="req-venue",
        score=8,
        justification="The hall seats 2,000 and the plan matches the regulations.",
        sources=("https://example.com/hall",),
        rubric_version="1",
        scored_by="agent",
    )


class _HandlerThatMustNotRun:
    def handle(self, command: ScoreRequirement) -> dict:
        raise AssertionError(f"handle was called with {command!r}")


def test_submit_requirement_score_rejects_a_score_outside_zero_to_ten() -> None:
    """T-AG-5, T-AG-6: out of range, bool, and non-integers never dispatch."""
    handler = _HandlerThatMustNotRun()

    for score in (-1, 11, True, 7.5, "8"):
        with pytest.raises(ValueError, match="score"):
            submit_requirement_score(
                "bid-1",
                "req-venue",
                score,  # type: ignore[arg-type]
                "A written justification.",
                ["https://example.com/hall"],
                "1",
                handler,
            )


class _RejectingScoreHandler:
    def handle(self, command: ScoreRequirement) -> dict:
        raise ScoringRejected("tender still open")


def test_submit_requirement_score_propagates_a_rejected_command() -> None:
    """An invariant failure stays the handler's exception."""
    with pytest.raises(ScoringRejected, match="tender still open"):
        submit_requirement_score(
            "bid-1",
            "req-venue",
            8,
            "A written justification.",
            ["https://example.com/hall"],
            "1",
            _RejectingScoreHandler(),
        )


def test_submit_requirement_score_rejects_blank_text() -> None:
    """Blank ids, justification, rubric version, or source entries never dispatch."""
    handler = _HandlerThatMustNotRun()
    text = "A written justification."
    source = ["https://example.com/hall"]
    cases = (
        ("bid_id", "", "req-venue", text, source, "1"),
        ("bid_id", "   ", "req-venue", text, source, "1"),
        ("requirement_id", "bid-1", "", text, source, "1"),
        ("requirement_id", "bid-1", "   ", text, source, "1"),
        ("justification", "bid-1", "req-venue", "", source, "1"),
        ("justification", "bid-1", "req-venue", "   ", source, "1"),
        ("rubric_version", "bid-1", "req-venue", text, source, ""),
        ("rubric_version", "bid-1", "req-venue", text, source, "   "),
        ("sources", "bid-1", "req-venue", text, [""], "1"),
        ("sources", "bid-1", "req-venue", text, ["   "], "1"),
    )

    for field, bid_id, requirement_id, justification, sources, rubric_version in cases:
        with pytest.raises(ValueError, match=field):
            submit_requirement_score(
                bid_id,
                requirement_id,
                8,
                justification,
                sources,
                rubric_version,
                handler,
            )
