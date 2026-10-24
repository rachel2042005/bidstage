"""Startup wiring for organizer semantic search.

Seam: `build_bid_search`. No test opens Chroma, Ollama, or SQL Server.
"""

from __future__ import annotations

import logging

from app import bootstrap

bootstrap.init()

from app.routes.bid_search import UnavailableBidSearch  # noqa: E402
from app.services.semantic_bid_search import SemanticBidSearch  # noqa: E402
from main import build_bid_search  # noqa: E402


class _Store:
    def __init__(self) -> None:
        self.heard = False

    def heartbeat(self) -> int:
        self.heard = True
        return 1


def test_unreachable_chroma_falls_back_without_raising(caplog) -> None:
    def fail(settings, embedder=None):
        raise ConnectionError("chroma down")

    with caplog.at_level(logging.WARNING, logger="bidstage.search"):
        search = build_bid_search(
            object(),
            embedder_factory=lambda settings: object(),
            store_factory=fail,
        )

    assert isinstance(search, UnavailableBidSearch)
    assert "unavailable" in caplog.text
    assert "chroma down" in caplog.text


def test_a_reachable_store_is_wrapped_and_indexed() -> None:
    holder: dict = {}

    def factory(settings, embedder=None):
        holder["embedder"] = embedder
        holder["store"] = _Store()
        return holder["store"]

    indexed: list = []

    def index(store):
        indexed.append(store)
        return 2

    search = build_bid_search(
        object(),
        embedder_factory=lambda settings: "embedder",
        store_factory=factory,
        index_concepts=index,
    )

    assert isinstance(search, SemanticBidSearch)
    assert holder["embedder"] == "embedder"
    assert holder["store"].heard is True
    assert indexed == [holder["store"]]


def test_an_index_failure_still_serves_search(caplog) -> None:
    def factory(settings, embedder=None):
        return _Store()

    def index(store):
        raise RuntimeError("sql down")

    with caplog.at_level(logging.WARNING, logger="bidstage.search"):
        search = build_bid_search(
            object(),
            embedder_factory=lambda settings: object(),
            store_factory=factory,
            index_concepts=index,
        )

    assert isinstance(search, SemanticBidSearch)
    assert "not indexed" in caplog.text
