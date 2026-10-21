"""Write side. See specs/02-architecture.md §3.

One handler per command. A handler replays the aggregate, checks invariants,
appends events, and projects affected read models in the same transaction.

A handler returns success or an ID — never a domain payload. Controllers
read session fields through GetUserById after RegisterUser / AuthenticateUser.

Commands: RegisterUser, AuthenticateUser, PublishTender, AddRequirement,
SubmitBid, WithdrawBid, CloseTender, ScoreRequirement, RevealPrices,
OverrideScore, SelectWinner, CancelTender.
"""
