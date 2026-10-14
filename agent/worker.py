"""Poll loop around `run_once` (`05-agent-spec.md` §3).

`run_forever` claims the next bid immediately when `run_once` returns 1.
It sleeps `interval` only when the claim is empty. `stop` is checked before
each claim. The call already in progress finishes, and the next bid stays
in the queue (`T-AG-36`, `T-AG-37`, `T-AG-38`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.runner import AssessmentQueue


def run_forever(
    queue: AssessmentQueue,
    *,
    interval: float,
    sleep: Callable[[float], None],
    stop: Callable[[], bool],
    requirements: Any,
    rubrics: Any,
    search: Any,
    web: Any,
    venue_model: Any,
    content_model: Any,
    experience_model: Any,
    host_model: Any,
    music_model: Any,
    submit: Any,
    complete: Any,
    cycle: Callable[..., int] | None = None,
) -> None:
    """Drain queued bids, then sleep while idle, until `stop` is set."""
    if cycle is None:
        from agent.runner import run_once

        cycle = run_once
    while not stop():
        worked = cycle(
            queue,
            requirements=requirements,
            rubrics=rubrics,
            search=search,
            web=web,
            venue_model=venue_model,
            content_model=content_model,
            experience_model=experience_model,
            host_model=host_model,
            music_model=music_model,
            submit=submit,
            complete=complete,
        )
        if worked == 0:
            sleep(interval)
