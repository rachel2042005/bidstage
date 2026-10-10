"""GetTenderRequirements (`02-architecture.md` §4 and §7).

Callers depend on this protocol. The SQL handler, which reads
`rm_tender_detail`, `rm_requirement`, and the approved-hosts reference table,
arrives with those projections.
"""

from __future__ import annotations

from typing import Protocol


class TenderRequirementsQuery(Protocol):
    def fetch(self, tender_id: str) -> dict | None:
        """`{tender, requirements, approved_hosts}`, or None when absent.

        The view may carry columns the tool must not return, including price.
        """
        ...


class SqlTenderRequirementsQuery:
    """SQL reader for `GetTenderRequirements`.

    The projections are not built yet, so a call fails closed. It does not
    read the events table (`NFR-CQRS-2`).
    """

    def fetch(self, tender_id: str) -> dict | None:
        raise NotImplementedError(
            "GetTenderRequirements SQL reader arrives with the projections."
        )
