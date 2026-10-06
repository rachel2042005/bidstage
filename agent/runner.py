"""Evaluation agent entry point — a process independent of the web app (NFR-AGT-2).

Run with: python -m agent.runner

Orchestration and sub-agents arrive in a later phase; see specs/05-agent-spec.md.
This module exists now to fix the process boundary and the TLS precondition.
"""

from __future__ import annotations

from app import bootstrap

bootstrap.init()

import time  # noqa: E402

from app.config import load_settings  # noqa: E402


def run_once() -> int:
    """One poll cycle. Returns the number of bids assessed.

    Phase 4 will claim bids from rm_bid_for_assessment, dispatch one sub-agent
    per requirement, and submit scores through the MCP tool. The agent never
    reads a price: its read model has no price column (NFR-AGT-4).
    """
    raise NotImplementedError("Agent orchestration lands in Phase 4.")


def main() -> None:
    settings = load_settings()
    print(f"Agent started; polling every {settings.agent_poll_seconds}s.")
    while True:
        try:
            run_once()
        except NotImplementedError as exc:
            print(exc)
            return
        time.sleep(settings.agent_poll_seconds)


if __name__ == "__main__":
    main()
