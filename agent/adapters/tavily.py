"""Tavily HTTP search behind `PageSearch` (`NFR-MCP-1`, `T-AG-45`, `T-AG-46`).

An empty key returns no pages and does not call Tavily. A key posts the query
and maps each hit's `url`, `title`, and `content`.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_SEARCH_URL = "https://api.tavily.com/search"


def post_tavily_search(
    api_key: str,
    query: str,
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """POST one search. `opener` stands in for `urlopen` in tests."""
    if opener is None:
        from app import bootstrap

        bootstrap.init()
        opener = urllib.request.urlopen
    body = json.dumps(
        {"api_key": api_key, "query": query},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        _SEARCH_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass(frozen=True)
class WebPage:
    """One Tavily hit. Evaluators read these three fields."""

    url: str
    title: str
    content: str


class TavilyPageSearch:
    """`PageSearch`. Callers read `url`, `title`, and `content`."""

    def __init__(
        self,
        api_key: str,
        *,
        post: Callable[[str, str], dict[str, Any]] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._post = post
        self._opener = opener

    def search(self, query: str) -> tuple[WebPage, ...]:
        if not self._api_key:
            return ()
        if self._post is None:
            body = post_tavily_search(self._api_key, query, opener=self._opener)
        else:
            body = self._post(self._api_key, query)
        pages: list[WebPage] = []
        for hit in body.get("results", []):
            pages.append(
                WebPage(
                    url=str(hit["url"]),
                    title=str(hit["title"]),
                    content=str(hit["content"]),
                )
            )
        return tuple(pages)
