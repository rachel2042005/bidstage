# 06 — Test Plan

**Status:** Derived from `01`–`05`. Every requirement and invariant declared there is discharged by a test here.

Priority order reflects risk, not layering. Scoring arithmetic and event-ordering invariants are where a defect would be both silent and fatal to the project's argument, so they come first.

---

## 1. Levels

| Level | Scope | External dependencies |
| --- | --- | --- |
| Unit | Scoring arithmetic, aggregate replay | None |
| Domain | Invariants and ordering over event streams | None |
| Integration | Command → events → read models | SQL Server |
| Agent | Orchestration, sub-agents, tools | Stubbed LLM and Tavily |
| End-to-end | Full demo scenarios | All, with seeded data |
| Environment | Network and process preconditions | Live |

The first two levels require no I/O because `app/domain/` is pure (`02-architecture.md` §2). That is the payoff of keeping aggregates free of infrastructure imports, and most of the suite should live there.

---

## 2. Unit — scoring arithmetic

Reproduces `04-scoring-model.md` exactly. These are table-driven.

| ID | Case | Expected |
| --- | --- | --- |
| `T-SC-1` | Worked example, supplier A weighted sum | 820 → quality 82.0 |
| `T-SC-2` | Worked example, supplier B weighted sum | 740 → quality 74.0 |
| `T-SC-3` | Price score, A at ₪900k against ₪700k minimum | 77.8 (to one decimal) |
| `T-SC-4` | Price score, cheapest qualified bid | exactly 100.0 |
| `T-SC-5` | Final score A, α = 0.6 | 80.3 |
| `T-SC-6` | Final score B, α = 0.6 | 84.4 |
| `T-SC-7` | Ranking order | B above A |
| `T-SC-8` | Disqualified cheaper bid excluded from the price pool | A and B unchanged from `T-SC-3`/`T-SC-5` |
| `T-SC-9` | Same bid set with the disqualified price wrongly included | A 71.4, B 72.9 — asserts the variant in `04` §7 and guards the boundary |
| `T-SC-10` | Threshold requirement scoring 5 | disqualified, remaining requirements unscored |
| `T-SC-11` | Threshold requirement scoring 6 | passes |
| `T-SC-12` | Quality 59.9 with all thresholds passed | disqualified by floor |
| `T-SC-13` | Quality exactly 60.0 | qualified |
| `T-SC-14` | α = 1 | final equals quality |
| `T-SC-15` | α = 0 | final equals price score, floor still enforced |
| `T-SC-16` | Tie on final score | higher quality ranks first |
| `T-SC-17` | Tie on final and quality | earlier submission ranks first |
| `T-SC-18` | Weights summing to 99 or 101 | publication rejected (`FR-ENT-3`) |
| `T-SC-19` | No rounding before final multiplication | full-precision path differs from a pre-rounded one and the former is used |

`T-SC-9` is the most valuable test in the file: it is the only one that fails if someone "simplifies" the price pool back to all bids.

---

## 3. Domain — event sourcing

### Replay

| ID | Case | Expected |
| --- | --- | --- |
| `T-ES-1` | Replay a tender stream twice | Identical state (`NFR-ES-3`) |
| `T-ES-2` | Unknown `EventType` during replay | Raises; never silently skipped (`03` §4) |
| `T-ES-3` | Events applied out of version order | Rejected |
| `T-ES-4` | Rebuild all read models from the stream | Byte-identical to incrementally projected values |
| `T-ES-5` | Two writers computing the same next `Version` | Second insert violates the primary key and retries (`NFR-ES-4`) |
| `T-ES-6` | Any `UPDATE`/`DELETE` against `Events` | Absent from the codebase; asserted by static check (`NFR-ES-2`) |

### Ordering invariants

One test per invariant in `03-domain-events.md` §3, each constructing an illegal stream and asserting rejection.

| ID | Invariant | Illegal stream under test |
| --- | --- | --- |
| `T-INV-1` | `INV-1` | `BidSubmitted` with no following `PriceOfferSealed` |
| `T-INV-2` | `INV-2` | `RequirementScored` before `TenderClosedForBids` |
| `T-INV-3` | `INV-3` | `PricesRevealed` with one bid still unassessed |
| `T-INV-4` | `INV-4` | `RequirementScored` after `PricesRevealed` |
| `T-INV-5` | `INV-5` | `FinalRankingCalculated` with no prior `PricesRevealed` |
| `T-INV-6` | `INV-6` | `WinnerSelected` naming a disqualified bid |
| `T-INV-7` | `INV-7` | Both `TenderFailed` and `PricesRevealed` on one tender |
| `T-INV-8` | `INV-8` | `ScoreOverridden` with no subsequent ranking |
| `T-INV-9` | `INV-9` | `TenderCreated` at a version other than 1 |
| `T-INV-10` | `INV-10` | An event following `BidWithdrawn` |

`T-INV-2` and `T-INV-4` are the double envelope expressed as tests. They are what makes "the agent never saw the price" a verified property rather than a design intention, and they belong in the defense.

---

## 4. Integration — commands and projections

| ID | Case | Expected |
| --- | --- | --- |
| `T-CMD-1` | `PublishTender` with weights at 100 | `TenderPublished` appended; search read model updated |
| `T-CMD-2` | `PublishTender` with a past deadline | Rejected (`FR-ENT-4`) |
| `T-CMD-3` | `SubmitBid` missing one requirement response | Rejected (`FR-ENT-5`) |
| `T-CMD-4` | `SubmitBid` | `BidSubmitted` and `PriceOfferSealed` in one transaction |
| `T-CMD-5` | `SubmitBid` after the deadline | Rejected (`FR-ENT-8`) |
| `T-CMD-6` | Second active bid from one supplier | Rejected (`FR-ENT-8`) |
| `T-CMD-7` | `WithdrawBid` after close | Rejected (`FR-ENT-7`) |
| `T-CMD-8` | Projection failure inside a handler | Event append rolls back with it (`NFR-CQRS-3`) |
| `T-CMD-9` | Gmail MCP unavailable on `SubmitBid` | Bid still committed (`FR-NOT-4`) |
| `T-CMD-10` | `OverrideScore` with empty justification | Rejected (`FR-EVAL-7`) |
| `T-CMD-11` | Override that changes the qualified set | Every bid's price score recomputed, not just the overridden one (`04` §9 step 4) |
| `T-CMD-12` | Any query handler touching `Events` | Absent; asserted by static check (`NFR-CQRS-2`) |

`T-CMD-11` targets the subtle bug in override handling: reordering is systemic, not local.

---

## 5. Authorization

| ID | Case | Expected |
| --- | --- | --- |
| `T-AUTH-1` | Supplier requests another supplier's bid by direct URL | 403, no content leaked (`FR-AUTH-5`) |
| `T-AUTH-2` | Supplier requests an organizer route | 403 (`FR-AUTH-4`) |
| `T-AUTH-3` | Organizer opens a tender they do not own | 403 (`FR-AUTH-6`) |
| `T-AUTH-4` | Organizer semantic search over bids | Results restricted to their own tenders (`FR-SRCH-6`) |
| `T-AUTH-5` | Any bid price read before `PricesRevealed` | Unavailable to organizer and agent alike (`FR-ENT-6`) |
| `T-AUTH-6` | Stored password | Salted hash; plaintext absent from database and logs (`FR-AUTH-2`) |
| `T-AUTH-7` | Role change attempt after registration | Rejected (`FR-AUTH-1`) |

`T-AUTH-4` is worth writing deliberately: semantic search is the easiest place to leak another tender's bids, because filtering happens after retrieval unless the query is scoped first.

---

## 6. Agent

LLM and Tavily are stubbed; no test spends money or depends on the network.

| ID | Case | Expected |
| --- | --- | --- |
| `T-AG-1` | `rm_bid_for_assessment` schema | No price column exists (`NFR-AGT-4`, `05` §2) |
| `T-AG-2` | `get_tender_requirements` response | Contains no price field for any bid |
| `T-AG-3` | `get_tender_requirements` on a tender with a `HOST` requirement | Includes `approved_hosts` |
| `T-AG-4` | `submit_requirement_score` called twice for one pair | Second call is a no-op; one `RequirementScored` exists |
| `T-AG-5` | `submit_requirement_score` with score 11 or −1 | Rejected |
| `T-AG-6` | Sub-agent returns a non-integer score | Schema violation; not rounded |
| `T-AG-7` | Factual verification with an empty `sources` list | Rejected and retried (`05` §6) |
| `T-AG-8` | Threshold requirement scores 4 | Orchestrator stops; later requirements never dispatched (`T-SC-10` counterpart) |
| `T-AG-9` | Transient API failure three times | Bid released unclaimed; no partial assessment persisted |
| `T-AG-10` | Crash after two of five scores | Next cycle resumes at the third requirement |
| `T-AG-11` | Two poll cycles overlapping | Bid claimed once; no double processing (`05` §3) |
| `T-AG-12` | Host absent from the approved list | Score 0 |
| `T-AG-13` | Venue capacity below the regulatory limit for the declared attendance | Score capped in the 0–4 band (`05` §5) |
| `T-AG-14` | Any arithmetic inside an agent module | Absent; quality computed in `app/domain/scoring.py` (project rule 2) |

### Score consistency

| ID | Case | Expected |
| --- | --- | --- |
| `T-AG-15` | Same bid scored five times against the live model | Each requirement's scores fall within one band; recorded, not asserted as equality |

`T-AG-15` is reported rather than pass/fail. `04-scoring-model.md` §10 claims stability within a band, not determinism, and the test should make exactly that claim measurable for the presentation.

---

## 7. Semantic search

| ID | Case | Expected |
| --- | --- | --- |
| `T-VEC-1` | Hebrew query against Hebrew bid content | Relevant bids returned (`FR-SRCH-5`) |
| `T-VEC-2` | `00-overview.md` §7 example, "bids including a tribute segment" | Matches the seeded bid containing one |
| `T-VEC-3` | Rubric retrieval | Returns one whole document, never a fragment (`NFR-VEC-3`) |
| `T-VEC-4` | Two processes against the Chroma server concurrently | Both succeed; no lock (`ENV-4`) |
| `T-VEC-5` | Vector store accessed outside `infrastructure/vector_store.py` | `chromadb` imported nowhere else (`NFR-VEC-5`) |
| `T-VEC-6` | Seed script run twice | Idempotent; no duplicate documents |

`tests/test_semantic_search_service.py` covers the service boundary. `T-VEC-3` asserts `get_rubric` returns the whole document stored at `rubric:{TYPE}`. Regulation search asserts cosine similarity as a literal: identical unit vectors score `1.0`, orthogonal unit vectors score `0.0`. Bid search passes `tender_id` into the store filter before `top_k` neighbours are chosen (`FR-SRCH-4`); a closer bid on another tender must not occupy the only result slot. Organizer ownership remains `T-AUTH-4`.

---

## 8. Seed data

One command builds everything (`ENV-6`). Scenarios are chosen so a demo reaches every branch.

**Tender 1 — "Annual Culture Awards Ceremony"**, five requirements per `04-scoring-model.md` §7, `VENUE` marked as threshold, α = 0.6.

| Bid | Design intent | Expected outcome |
| --- | --- | --- |
| Supplier A | Strong concept, expensive, ₪900,000 | Quality 82, final 80.3, ranked second |
| Supplier B | Solid concept, cheap, ₪700,000 | Quality 74, final 84.4, **winner** |
| Supplier C | Undersized venue, cheapest at ₪500,000 | Disqualified on the `VENUE` threshold; excluded from the price pool |
| Supplier D | Weak across the board, ₪800,000 | Quality below 60; disqualified by the floor |

This single tender demonstrates the weighting deciding against higher quality, both disqualification gates, and the qualified-only price pool.

**Tender 2 — "Regional Sports Final"**: two bids, both failing the quality floor. Ends in `TenderFailed` with no `PricesRevealed`, exercising the degenerate path in `04` §8.

Seeded reference data: the approved host list, five rubrics, and a small regulations corpus.

---

## 9. End-to-end

| ID | Scenario |
| --- | --- |
| `T-E2E-1` | Organizer registers, publishes tender 1, four suppliers bid, deadline passes, agent scores all bids, prices reveal, ranking matches §8, organizer selects B, emails dispatched |
| `T-E2E-2` | Organizer overrides one of A's requirement scores upward; ranking recomputes and the comparison table reorders |
| `T-E2E-3` | Tender 2 runs to `TenderFailed`; no prices are ever revealed |
| `T-E2E-4` | "Evaluate Now" produces an assessment without waiting for the poll (`FR-EVAL-10`) |
| `T-E2E-5` | Prices render as "sealed" before reveal and as values after (`FR-TBL-4`) |
| `T-E2E-6` | Dashboard counts and countdowns match the seeded tenders (`FR-DASH-1` … `FR-DASH-3`) |
| `T-E2E-7` | Full audit replay of tender 1 reconstructs the final ranking from events alone |

`T-E2E-7` is the Event Sourcing claim end to end: the outcome is reproducible from history with no read model involved.

---

## 10. Environment smoke tests

Run before any demo. Each maps to a constraint in `01-requirements.md` §4.

| ID | Check | Constraint |
| --- | --- | --- |
| `T-ENV-1` | `truststore.inject_into_ssl()` runs before any client; an OpenAI call succeeds | `ENV-1` |
| `T-ENV-2` | Entry points import `truststore` as their first statement | `ENV-1` |
| `T-ENV-3` | Somee connection succeeds over ODBC Driver 17 | `ENV-2` |
| `T-ENV-4` | Database queried within the last 30 days | `ENV-2` |
| `T-ENV-5` | Events table size tracked against the 30 MB cap | `ENV-2` |
| `T-ENV-6` | No embedding column or vector data in SQL Server | `ENV-3` |
| `T-ENV-7` | Chroma reachable on `/api/v2/heartbeat`; no `PersistentClient` in the codebase | `ENV-4` |
| `T-ENV-8` | Seed script rebuilds SQL and Chroma from empty | `ENV-6` |

`T-ENV-1` would have caught the certificate failure before it reached the agent, which is why it is a standing check rather than a one-off fix.

---

## 11. Traceability

| Area | Requirements | Tests |
| --- | --- | --- |
| Authentication and access | `FR-AUTH-1` … `FR-AUTH-6` | `T-AUTH-1` … `T-AUTH-7` |
| Search | `FR-SRCH-1` … `FR-SRCH-6` | `T-VEC-1`, `T-VEC-2`, `T-AUTH-4` |
| Detail views | `FR-DET-1` … `FR-DET-5` | `T-E2E-1`, `T-E2E-2`, `T-E2E-5` |
| Comparison table | `FR-TBL-1` … `FR-TBL-4` | `T-E2E-2`, `T-E2E-5`, `T-SC-7` |
| Dashboard | `FR-DASH-1` … `FR-DASH-5` | `T-E2E-6` |
| Data entry | `FR-ENT-1` … `FR-ENT-8` | `T-CMD-1` … `T-CMD-7`, `T-SC-18` |
| Evaluation and award | `FR-EVAL-1` … `FR-EVAL-10` | `T-SC-10` … `T-SC-13`, `T-AG-8`, `T-E2E-1`, `T-E2E-3`, `T-E2E-4` |
| Notifications | `FR-NOT-1` … `FR-NOT-4` | `T-CMD-9`, `T-E2E-1` |
| Vector database | `NFR-VEC-1` … `NFR-VEC-5` | `T-VEC-1` … `T-VEC-6` |
| CQRS | `NFR-CQRS-1` … `NFR-CQRS-3` | `T-CMD-8`, `T-CMD-12`, `T-ES-4` |
| Event Sourcing | `NFR-ES-1` … `NFR-ES-5` | `T-ES-1` … `T-ES-6`, `T-INV-1` … `T-INV-10`, `T-E2E-7` |
| External MCP | `NFR-MCP-1`, `NFR-MCP-2` | `T-CMD-9`, `T-E2E-1` |
| Own tools | `NFR-TOOL-1` | `T-AG-2` … `T-AG-5` |
| Deep Agent | `NFR-AGT-1` … `NFR-AGT-4` | `T-AG-1` … `T-AG-15` |
| Determinism | `NFR-DET-1` | `T-AG-14`, `T-SC-1` … `T-SC-19` |
| Environment | `ENV-1` … `ENV-6` | `T-ENV-1` … `T-ENV-8` |
