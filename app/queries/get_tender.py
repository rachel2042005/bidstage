"""GetTenderDetails — rm_tender_detail and rm_requirement only (NFR-CQRS-2)."""

from __future__ import annotations

from dataclasses import dataclass

from app.projections.tenders import RequirementRecord, TenderSummary, TendersProjection


@dataclass(frozen=True)
class TenderDetails:
    tender: TenderSummary
    requirements: tuple[RequirementRecord, ...]

    @property
    def weight_total(self) -> int:
        return sum(item.weight for item in self.requirements)


def get_tender_details(tenders: TendersProjection, tender_id: str) -> TenderDetails | None:
    tender = tenders.get(tender_id)
    if tender is None:
        return None
    return TenderDetails(
        tender=tender,
        requirements=tuple(tenders.requirements_for(tender_id)),
    )
