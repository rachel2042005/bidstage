"""Write side. See specs/02-architecture.md §3.

One handler per command. A handler replays the aggregate, checks invariants,
appends events, and projects affected read models in the same transaction.

Commands: PublishTender, AddRequirement, SubmitBid, WithdrawBid, CloseTender,
ScoreRequirement, RevealPrices, OverrideScore, SelectWinner, CancelTender.
"""
