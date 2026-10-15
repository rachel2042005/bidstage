"""Read models projected from events. See specs/02-architecture.md §4.

Projections run synchronously inside the command handler that appended the
event (NFR-CQRS-3), and are fully rebuildable via EventStore.read_all().

Models: rm_tender_search, rm_tender_detail, rm_requirement, rm_bid_ranking,
rm_dashboard, rm_requirement_score, rm_bid_assessment, and rm_bid_for_assessment.

`rm_bid_assessment`, `rm_requirement_score`, and `rm_bid_ranking` are projected
by `SqlCompleteAssessmentHandler` and `SqlRevealPricesHandler`.
`rm_tender_detail`, `rm_requirement`, and `approved_hosts` are created with the
schema. `PublishTender` fills the tender rows later. `SqlTenderRequirementsQuery`
reads them.

rm_bid_for_assessment serves the agent rather than a query, and deliberately
has no price column — that absence is what enforces NFR-AGT-4.
`enqueue_bid_for_assessment`, `release_stalled_claims`, and `SqlAssessmentQueue`
live in `bid_for_assessment.py`. Completion marks the row `completed`.
"""
