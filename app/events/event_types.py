"""Event type names. Catalogue and payloads: specs/03-domain-events.md §2.

Names are append-only. An existing name is never renamed or repurposed, because
history already written under it must stay readable.
"""

from __future__ import annotations

from typing import Final

# Tender stream
TENDER_CREATED: Final = "TenderCreated"
REQUIREMENT_ADDED: Final = "RequirementAdded"
TENDER_PUBLISHED: Final = "TenderPublished"
TENDER_CLOSED_FOR_BIDS: Final = "TenderClosedForBids"
PRICES_REVEALED: Final = "PricesRevealed"
FINAL_RANKING_CALCULATED: Final = "FinalRankingCalculated"
WINNER_SELECTED: Final = "WinnerSelected"
TENDER_FAILED: Final = "TenderFailed"
TENDER_CANCELLED: Final = "TenderCancelled"

# Bid stream
BID_SUBMITTED: Final = "BidSubmitted"
PRICE_OFFER_SEALED: Final = "PriceOfferSealed"
BID_WITHDRAWN: Final = "BidWithdrawn"
REQUIREMENT_SCORED: Final = "RequirementScored"
BID_DISQUALIFIED: Final = "BidDisqualified"
QUALITY_ASSESSMENT_COMPLETED: Final = "QualityAssessmentCompleted"
SCORE_OVERRIDDEN: Final = "ScoreOverridden"

TENDER_EVENTS: Final = frozenset(
    {
        TENDER_CREATED,
        REQUIREMENT_ADDED,
        TENDER_PUBLISHED,
        TENDER_CLOSED_FOR_BIDS,
        PRICES_REVEALED,
        FINAL_RANKING_CALCULATED,
        WINNER_SELECTED,
        TENDER_FAILED,
        TENDER_CANCELLED,
    }
)

BID_EVENTS: Final = frozenset(
    {
        BID_SUBMITTED,
        PRICE_OFFER_SEALED,
        BID_WITHDRAWN,
        REQUIREMENT_SCORED,
        BID_DISQUALIFIED,
        QUALITY_ASSESSMENT_COMPLETED,
        SCORE_OVERRIDDEN,
    }
)

ALL_EVENTS: Final = TENDER_EVENTS | BID_EVENTS
