"""GetDashboardStats — rm_dashboard only (FR-DASH-5, NFR-CQRS-2)."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.tender import CANCELLED, DECIDED, DRAFT, FAILED, OPEN, UNDER_EVALUATION
from app.projections.tenders import DashboardEntry


@dataclass(frozen=True)
class Dashboard:
    draft: tuple[DashboardEntry, ...]
    open: tuple[DashboardEntry, ...]
    under_evaluation: tuple[DashboardEntry, ...]
    decided: tuple[DashboardEntry, ...]
    failed: tuple[DashboardEntry, ...]
    cancelled: tuple[DashboardEntry, ...]


def get_dashboard(tenders, organizer_id: str) -> Dashboard:
    grouped: dict[str, list[DashboardEntry]] = {
        DRAFT: [],
        OPEN: [],
        UNDER_EVALUATION: [],
        DECIDED: [],
        FAILED: [],
        CANCELLED: [],
    }
    for entry in tenders.dashboard_for(organizer_id):
        grouped.setdefault(entry.status, []).append(entry)
    return Dashboard(
        draft=tuple(grouped[DRAFT]),
        open=tuple(grouped[OPEN]),
        under_evaluation=tuple(grouped[UNDER_EVALUATION]),
        decided=tuple(grouped[DECIDED]),
        failed=tuple(grouped[FAILED]),
        cancelled=tuple(grouped[CANCELLED]),
    )
