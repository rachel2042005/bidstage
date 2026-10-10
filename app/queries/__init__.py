"""Read side. See specs/02-architecture.md §4.

Handlers read exclusively from read models. A query handler that touches the
events table violates NFR-CQRS-2.

Queries: SearchTenders, GetTenderDetails, GetBidComparison, GetDashboardStats,
GetBidAssessment, GetTenderRequirements.
"""
