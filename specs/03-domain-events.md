# 03 — Domain Events

**Status:** Derived from `00-overview.md`. Satisfies `NFR-ES-1` … `NFR-ES-5`.

The event stream is the system of record. Everything else — read models, dashboards, rankings — is a derived view that can be thrown away and rebuilt. This document is therefore the most load-bearing specification in the set.

---

## 1. Streams

| Stream | `StreamId` | Lifetime |
| --- | --- | --- |
| Tender | Tender UUID | Creation to decided, failed or cancelled |
| Bid | Bid UUID | Submission to qualified, disqualified or withdrawn |

Requirement scores live on the **bid** stream, not the tender stream. A bid's complete assessment history is therefore one contiguous replay, which is what `GetBidAssessment` needs.

`Version` starts at 1 and increments by exactly 1 per stream. Gaps are a defect, not a tolerated condition.

---

## 2. Event catalogue

### Tender stream

| Event | Payload | Emitted by | Preconditions |
| --- | --- | --- | --- |
| `TenderCreated` | `name`, `event_type`, `event_date`, `region`, `estimated_budget`, `deadline`, `alpha`, `organizer_id` | `PublishTender` (draft step) | `alpha` in `[0,1]`; `deadline` in the future |
| `RequirementAdded` | `requirement_id`, `type`, `weight`, `is_threshold`, `description` | `AddRequirement` | Tender not yet published; `type` is one of the five; `0 < weight ≤ 100` |
| `TenderPublished` | `published_at` | `PublishTender` | Weights sum to exactly 100; at least one requirement; deadline still future |
| `TenderClosedForBids` | `closed_at`, `bid_count` | `CloseTender` | Tender was published; deadline passed, or organizer closed it explicitly |
| `PricesRevealed` | `prices: [{bid_id, amount}]` | `RevealPrices` | Every bid is in a terminal assessment state; at least one bid qualified |
| `FinalRankingCalculated` | `ranking: [{bid_id, quality, price, price_score, final_score, rank}]` | `RevealPrices` / `OverrideScore` | Immediately follows `PricesRevealed` or an override |
| `WinnerSelected` | `bid_id`, `selected_by`, `selected_at` | `SelectWinner` | Ranking exists; the chosen bid is qualified |
| `TenderFailed` | `reason`, `failed_at` | `CloseTender` / assessment completion | No bid passed both thresholds and the quality floor |
| `TenderCancelled` | `reason`, `cancelled_at` | `CancelTender` | Not already decided or failed |

### Bid stream

| Event | Payload | Emitted by | Preconditions |
| --- | --- | --- | --- |
| `BidSubmitted` | `tender_id`, `supplier_id`, `concept`, `responses: [{requirement_id, answer}]` | `SubmitBid` | Tender open; deadline not passed; a response for every requirement; supplier has no active bid |
| `PriceOfferSealed` | `price_amount`, `currency`, `sealed_at` | `SubmitBid` | Written in the same transaction as `BidSubmitted` |
| `BidWithdrawn` | `withdrawn_at` | `WithdrawBid` | Tender still open for bids |
| `RequirementScored` | `requirement_id`, `score`, `justification`, `sources`, `scored_by: "agent"`, `rubric_version` | `submit_requirement_score` | `0 ≤ score ≤ 10`; not already scored; tender under evaluation |
| `BidDisqualified` | `reason`, `failed_requirement_id?`, `quality_score?` | assessment completion | A threshold requirement scored below its pass mark, **or** quality below 60 |
| `QualityAssessmentCompleted` | `quality_score`, `requirement_scores: [{requirement_id, score, weight}]` | assessment completion | Every requirement scored; no threshold failure |
| `ScoreOverridden` | `requirement_id`, `old_score`, `new_score`, `justification`, `overridden_by` | `OverrideScore` | Assessment complete; justification non-empty |

`PriceOfferSealed` is a separate event from `BidSubmitted` on purpose. The price is not a field of the bid; it is a sealed envelope with its own lifecycle, and separating it makes "the price was never read before reveal" a property you can demonstrate from the stream rather than assert.

---

## 3. Ordering invariants

These are the rules that make the audit trail meaningful. Each is a test in `06-tests.md`.

| ID | Invariant |
| --- | --- |
| `INV-1` | `PriceOfferSealed` always immediately follows `BidSubmitted` on the same stream. |
| `INV-2` | No `RequirementScored` exists on a bid stream before `TenderClosedForBids` on its tender. The agent cannot have scored a bid while bidding was still open. |
| `INV-3` | `PricesRevealed` occurs **after** every bid reached `QualityAssessmentCompleted` or `BidDisqualified`. A single unassessed bid blocks reveal. |
| `INV-4` | No `RequirementScored` occurs after `PricesRevealed`. Scores are never produced with price knowledge. |
| `INV-5` | `FinalRankingCalculated` is always preceded by `PricesRevealed` on the same tender. |
| `INV-6` | `WinnerSelected` names a bid that reached `QualityAssessmentCompleted`, never a disqualified one. |
| `INV-7` | `TenderFailed` and `PricesRevealed` are mutually exclusive on one tender. |
| `INV-8` | `ScoreOverridden` is always followed by a fresh `FinalRankingCalculated`. |
| `INV-9` | `TenderCreated` is version 1 of its stream; `BidSubmitted` is version 1 of its stream. |
| `INV-10` | At most one `BidWithdrawn` per bid stream, and nothing follows it. |

`INV-2` and `INV-4` together are the formal statement of the double envelope. They are the strongest argument that Event Sourcing is doing real work here rather than decorating a CRUD application, and they are worth showing directly in the defense.

---

## 4. Replay

Aggregate state is a left fold over the stream (`NFR-ES-3`):

```
state = reduce(apply, events_ordered_by_version, initial_state)
```

`apply` is a pure function with no I/O. Rules:

- An unrecognized `EventType` raises rather than being skipped. Silent skipping turns a deploy mistake into quiet data loss.
- Replay never consults a read model.
- Replaying the same stream twice yields identical state.
- Read models are rebuildable: truncate and replay all streams in `global_seq` order, the store's total append order across streams.

The seed script (`ENV-6`) uses exactly this path, which is why a Somee deletion (`ENV-2`) costs minutes.

---

## 5. State derivation

Status is derived, never stored on the write side.

**Tender**

| Status | Derived from |
| --- | --- |
| Draft | `TenderCreated`, no `TenderPublished` |
| Open for bids | `TenderPublished`, no `TenderClosedForBids` |
| Under evaluation | `TenderClosedForBids`, no `FinalRankingCalculated` / `TenderFailed` |
| Decided | `WinnerSelected` |
| Failed | `TenderFailed` |
| Cancelled | `TenderCancelled` |

**Bid**

| Status | Derived from |
| --- | --- |
| Submitted | `BidSubmitted`, nothing further |
| Withdrawn | `BidWithdrawn` |
| Under assessment | At least one `RequirementScored`, not terminal |
| Disqualified | `BidDisqualified` |
| Qualified | `QualityAssessmentCompleted` |

Read models store the derived status as a column for query speed; that column is a projection output, never an authority.

---

## 6. Payload conventions

- `event_data` is a JSON object, never a bare scalar, so fields can be added without a migration.
- `metadata` is a separate nullable JSON column for cross-cutting context — `actor`, `actor_role`, `correlation_id`. Domain facts belong in `event_data`; who did it and which request it belonged to belong in `metadata`. The `scored_by`, `selected_by` and `overridden_by` fields listed in §2 stay in `event_data`, because *who scored it* is part of the domain fact, not request plumbing.
- Money is an integer minor unit plus a currency code. No floats.
- Timestamps are UTC ISO-8601; the Hebrew UI localizes on render.
- Hebrew text is stored as-is in `NVARCHAR(MAX)`, unescaped and unnormalized.
- `sources` is a list of absolute URLs, kept so `FR-DET-2` can render them as links and so a score stays auditable after the fact.
- `rubric_version` is recorded on every `RequirementScored`, so a score can be explained against the rubric that produced it even after the rubric changes.

### Adding an event type

Additive only. Append the new type, teach `apply` to handle it, extend projections. Never rewrite history, never repurpose an existing type's meaning. A correction is a new compensating event — which is precisely what `ScoreOverridden` is, rather than an edit of `RequirementScored`.
