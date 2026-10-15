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
| `T-SC-9` | Same bid set with the disqualified price wrongly included | A 71.4, B 73.0 — asserts the variant in `04` §7 and guards the boundary |
| `T-SC-10` | Threshold requirement scoring 5 | disqualified, remaining requirements unscored |
| `T-SC-11` | Threshold requirement scoring 6 | passes |
| `T-SC-12` | Quality 59.9 with all thresholds passed | disqualified by floor |
| `T-SC-13` | Quality exactly 60.0 | qualified |
| `T-SC-14` | α = 1 on the worked example | A ranks above B; finals are 82.0 and 74.0; price scores still display as 77.8 and 100.0 |
| `T-SC-15` | α = 0, with a disqualified ₪500,000 bid in the input | B ranks above A; finals are 100.0 and 77.8; the disqualified bid is absent |
| `T-SC-16` | Finals both 84.4 (quality 82 at ₪1,000,000 and quality 74 at ₪880,000), α = 0.6 | higher quality ranks first, even though it was later and second in the input |
| `T-SC-17` | Equal final and equal quality | earlier UTC `submitted_at` ranks first, even though it was second in the input |
| `T-SC-18` | Weights summing to 99 or 101 | publication rejected (`FR-ENT-3`) |
| `T-SC-19` | No rounding before final multiplication | full-precision path differs from a pre-rounded one and the former is used |
| `T-SC-20` | `price_score` given 0, −1, or a bool | `ValueError`; a price must be an integer greater than 0 |

`T-SC-9` is the most valuable test in the file: it is the only one that fails if someone "simplifies" the price pool back to all bids.

`tests/test_scoring.py` covers `T-SC-1` and `T-SC-2` at `quality`. The expected values are the literals 82.0 and 74.0. The same file covers `price_score`, `final_score`, and `rank_bids`: `T-SC-4` and `T-SC-3` for the price scores 100.0 and 77.8, `T-SC-6` and `T-SC-5` for the finals 84.4 and 80.3, `T-SC-19` so A's final is not `0.6 × 82 + 0.4 × 77.8`, `T-SC-7` for B ranking above A, `T-SC-8` and `T-SC-9` for the qualified-only pool, `T-SC-14` and `T-SC-15` for α = 1 and α = 0, `T-SC-16` and `T-SC-17` for the quality and submission tie-breaks, and `T-SC-20` for a non-positive price. Displayed figures use `round(value, 1)`. The computed values stay at full precision.

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
| `T-CMD-13` | `QualityAssessmentCompleted` at quality 90.0 | Next bid version; `rm_bid_assessment.status` is `qualified`; requirement rows match the payload |
| `T-CMD-14` | Threshold `BidDisqualified` | `reason` `threshold`, `failed_requirement_id` set, `quality_score` null, no requirement rows |
| `T-CMD-15` | Second terminal command for one bid | Raises; the bid stream stays one event long |
| `T-CMD-16` | `RevealPrices` while a bid is not terminal | Raises; the tender stream gains nothing (`INV-3`) |
| `T-CMD-17` | Worked example, both bids qualified, α = 0.6 | `PricesRevealed` then `FinalRankingCalculated`; B rank 1 at 84.4, A rank 2 at 80.3 |
| `T-CMD-18` | Every bid disqualified | `TenderFailed` with `reason` `no_qualified_bids`; no `PricesRevealed` |
| `T-CMD-19` | `QualityAssessmentCompleted` after the bid was queued | Queue `status` is `completed`; a following claim returns nothing |

`T-CMD-11` targets the subtle bug in override handling: reordering is systemic, not local.

`tests/test_assessment_sql.py` covers `T-CMD-13` through `T-CMD-18` against SQL Server. The suite skips when `SOMEE_DB_CONNECTION_STRING` is unset. Each test uses a fresh stream id and does not delete. `SqlCompleteAssessmentHandler` is the seam for the terminal events. `SqlRevealPricesHandler` is the seam for reveal and `TenderFailed`. Streams are read back through `EventStore`. Read models are read with `SELECT`.

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
| `T-AG-1` | `rm_bid_for_assessment` schema | Columns are `bid_id`, `tender_id`, `supplier_id`, `concept`, `responses_json`, `status`, `queued_at`, `claimed_at`. No column name contains `price` (`NFR-AGT-4`, `05` §2) |
| `T-AG-2` | `get_tender_requirements` response | Contains no price field for any bid |
| `T-AG-3` | `get_tender_requirements` on a tender with a `HOST` requirement | Includes `approved_hosts` |
| `T-AG-4` | `submit_requirement_score` called twice for one pair | Second call is a no-op; one `RequirementScored` exists |
| `T-AG-5` | `submit_requirement_score` with score 11 or −1 | Rejected |
| `T-AG-6` | Sub-agent returns a non-integer score | Schema violation; not rounded |
| `T-AG-7` | Factual verification with an empty `sources` list | Rejected and retried (`05` §6) |
| `T-AG-8` | Threshold requirement scores 4 | Orchestrator stops; later requirements never dispatched (`T-SC-10` counterpart) |
| `T-AG-9` | Transient API failure three times | Bid released unclaimed; no partial assessment persisted |
| `T-AG-10` | Crash after two of five scores | Next cycle resumes at the third requirement |
| `T-AG-11` | Two connections claim one pending bid | One receives that bid; the other receives nothing (`05` §3) |
| `T-AG-12` | A real host is named and is absent from the approved list; the model proposes 9 | Score is 1 or 2 (`05` §5) |
| `T-AG-13` | A retrieved police, fire, licence, or accessibility obligation is breached, and the model proposes 9 | Score is an integer in 0–4 (`05` §5) |
| `T-AG-14` | Any arithmetic inside an agent module | Absent; quality computed in `app/domain/scoring.py` (project rule 2) |
| `T-AG-16` | Official capacity is verified and below the tender figure; no retrieved obligation is breached; the model proposes 9 | Score is 3 or 4 |
| `T-AG-17` | The supplier's capacity claim is disproved; the model proposes 9 | Score is 0 |
| `T-AG-18` | The capacity claim is inconclusive; the model proposes 9 | Score is 5 or 6 |
| `T-AG-19` | Capacity is verified, meets the tender, and breaches nothing; the model proposes 4 | Score is an integer in 7–10 |
| `T-AG-20` | Orchestrator scores a tender whose `VENUE` threshold scores 9, followed by `CONTENT` and `EXPERIENCE` | Three `RequirementScored` commands, in list order; each `rubric_version` is that type's stored rubric |
| `T-AG-21` | Schedule and concept are both present and on-theme, the central names are independently verified, and the model proposes 4 | Score is 9 or 10 (`05` §5) |
| `T-AG-22` | The schedule contradicts itself beside a relevant concept, and the model proposes 9 | Score is 3 or 4 |
| `T-AG-23` | A named performer or work is disproved, and the model proposes 9 | Score is 0 |
| `T-AG-24` | Three or more outdoor or stadium productions are verified, none are disproved, and the model proposes 4 | Score is 9 or 10 (`05` §5) |
| `T-AG-25` | Outdoor or stadium events are claimed, none are verified and none are disproved, and the model proposes 9 | Score is 5 or 6 |
| `T-AG-26` | One production is disproved while three outdoor shows are verified, and the model proposes 9 | Score is 0 |
| `T-AG-27` | `run_once` claims one bid whose three requirements score 9 | `QualityAssessmentCompleted` with quality 90.0; a second claim returns nothing |
| `T-AG-28` | The `VENUE` threshold scores 4 | `BidDisqualified` with `reason` `threshold` and `failed_requirement_id`; later models are not assessed |
| `T-AG-29` | Every requirement is scored and quality is 50.0 | `BidDisqualified` with `reason` `quality_floor` and `quality_score` 50.0; no `failed_requirement_id` |
| `T-AG-30` | Primary and backup are both on the approved list, neither has a same-date conflict, and the model proposes 4 | Score is 9 or 10 (`05` §5) |
| `T-AG-31` | The primary host is listed and has a same-date conflict, the backup is also listed, and the model proposes 9 | Score is 3 or 4 |
| `T-AG-32` | A live performance has a complete rider, the central acts are verified, and the model proposes 4 | Score is 9 or 10 (`05` §5) |
| `T-AG-33` | The tender requires a live performance and the bid has none, and the model proposes 9 | Score is 3 or 4 |
| `T-AG-34` | A named act is disproved while the live program and rider would otherwise meet the 9–10 rule, and the model proposes 9 | Score is 0 |
| `T-AG-35` | Orchestrator scores `VENUE`, `CONTENT`, `HOST`, `MUSIC`, and `EXPERIENCE` in that order, and the `VENUE` threshold scores 9 | Five `RequirementScored` commands, in list order; each `rubric_version` is that type's stored rubric |
| `T-AG-36` | Two bids queued, then an empty claim | Both bids run with no sleep between them; one sleep of the poll interval follows the empty claim |
| `T-AG-37` | Queue is empty | `run_once` is entered; `sleep` receives the poll interval |
| `T-AG-38` | Stop flag set during the first bid | That bid finishes; the next bid is not claimed |
| `T-AG-39` | One pending bid whose venue answer is `היכל` | `claim` returns that bid with `answers == {"req-venue": "היכל"}`; the row is `claimed` with `claimed_at` set; the next claim returns nothing |
| `T-AG-40` | `PriceOfferSealed` amount `900000` is on the bid stream, and the queue row is written from `BidSubmitted` fields | The claimed work and every queue-row value leave out `900000` |
| `T-AG-41` | `main(["--once"])` with a fake runtime | `run_once` is called once with that queue, `complete`, and the five models; `run_forever` is not called |
| `T-AG-42` | `main([])` with a fake runtime | `run_forever` receives that runtime and `interval` equal to `settings.agent_poll_seconds`; `main` does not call `run_once` |
| `T-AG-43` | A claimed row whose `claimed_at` is strictly older than the timeout | `release_stalled_claims` sets `status` to `pending` and `claimed_at` to null; the next `claim` returns that bid |
| `T-AG-44` | A claimed row whose `claimed_at` is inside the timeout | The row stays `claimed` with `claimed_at` set; the next `claim` returns nothing |
| `T-AG-45` | `TAVILY_API_KEY` is empty | `search` returns `()` and does not call Tavily |
| `T-AG-46` | Tavily returns one hit for `היכל` | The page is `url` `https://example.com/hall`, `title` `Hall`, `content` `5000 seats`. The request is `POST https://api.tavily.com/search` and carries that query and the key |
| `T-AG-47` | Venue model JSON with `proposed_score` 4 | `assess_venue` returns that `VenueAssessment` and the call's temperature is 0. Score 11 and `true` are rejected |
| `T-AG-48` | Content model JSON with `proposed_score` 4 | `assess_content` returns that `ContentAssessment` at temperature 0 |
| `T-AG-49` | Host model JSON with `proposed_score` 4 | `assess_host` returns that `HostAssessment` at temperature 0 |
| `T-AG-50` | Music model JSON with `proposed_score` 4 | `assess_music` returns that `MusicAssessment` at temperature 0 |
| `T-AG-51` | Experience model JSON with `proposed_score` 4 | `assess_experience` returns that `ExperienceAssessment` at temperature 0 |
| `T-AG-52` | `assess` is handed each material type | The same assessor returns that type's assessment |
| `T-AG-53` | `AGENT_MODEL_PROVIDER` | `ollama` selects `qwen3:8b` and `http://localhost:11434/v1`. `openai` selects `gpt-4o-mini`. `ollama` plus `AGENT_MODEL=gpt-4o-mini` is rejected |
| `T-AG-54` | `build_runtime(settings, requirements=..., submit=...)` with stand-ins for the queue and the vector store | `search` is the rubric service, `web.search` returns `()` when the key is empty, and one assessor is all five model ports. `requirements` and `submit` are the objects passed in |

`tests/test_mcp_server.py` covers `T-AG-2` and `T-AG-3` against an in-memory `GetTenderRequirements` query. A price column on the view is absent from the payload. A `HOST` requirement includes the host list the query supplied. A blank id raises `ValueError` before lookup. An unknown id raises `TenderNotFound`. The same file covers `submit_requirement_score`: the dispatched `ScoreRequirement` is a known literal and the handler's `{"accepted": True, "event_version": 3}` is returned unchanged; scores outside 0–10, bools, and non-integers raise `ValueError` (`T-AG-5`, `T-AG-6`); blank text fields raise `ValueError` before dispatch; `ScoringRejected` propagates. `T-AG-4` waits for the SQL handler.

`tests/test_venue_evaluator.py` covers `T-AG-13`, `T-AG-16`, `T-AG-17`, `T-AG-18`, and `T-AG-19` at `evaluate_venue`. Regulations, web search, and the model are fakes. The model reports the constraining finding only when it was given the whole rubric plus the regulation text and the web page. `T-AG-19` proposes 4, which sits below the 7–10 band.

`tests/test_content_evaluator.py` covers `T-AG-21`, `T-AG-22`, and `T-AG-23` at `evaluate_content`. Past concepts, web search, and the model are fakes. `search_bids` returns the precedent text only when `tender_id` is omitted. The model reports the constraining finding only when it was given the whole rubric plus that text and the web page. `T-AG-21` proposes 4, which sits below the 9–10 band. `T-AG-23` reports the schedule and concept as sound and the named performer as disproved, and the score is 0.

`tests/test_experience_evaluator.py` covers `T-AG-24`, `T-AG-25`, and `T-AG-26` at `evaluate_experience`. Portfolio search, web search, and the model are fakes. `search_portfolios` returns the supplier's text only when `supplier_id` matches. The model reports the constraining finding only when it was given the whole rubric plus that text and the web page. `T-AG-24` proposes 4, which sits below the 9–10 band. `T-AG-26` also reports three verified outdoor shows, so a first-match that skips the disproved-production rule would pin the score into 9–10.

`tests/test_host_evaluator.py` covers `T-AG-12`, `T-AG-30`, and `T-AG-31` at `evaluate_host`. Web search and the model are fakes. Membership is `strip().casefold()` inside the function. The model reports the extracted names only when it was given the whole rubric, the approved list, and the web page. `T-AG-12` proposes 9 for a named host who is not on the list, which sits above the 1–2 band. `T-AG-30` proposes 4, which sits below the 9–10 band, and the extracted names differ from the list by case and surrounding spaces. `T-AG-31` also lists the backup and reports no backup conflict, so a first-match that skips the same-date conflict rule would pin the score into 9–10.

`tests/test_music_evaluator.py` covers `T-AG-32`, `T-AG-33`, and `T-AG-34` at `evaluate_music`. Past concepts, web search, and the model are fakes. `search_bids` returns the precedent text only when `tender_id` is omitted. The model reports the constraining finding only when it was given the whole rubric plus that text and the web page. `T-AG-32` proposes 4, which sits below the 9–10 band. `T-AG-34` reports a live performance and a complete rider and a disproved act, and the score is 0.

`tests/test_orchestrator.py` covers `T-AG-20`, `T-AG-8`, and `T-AG-35` at `score_bid`. `get_tender_requirements` and `submit_requirement_score` run against in-memory doubles. Each rubric is loaded through `SemanticSearchService` from an in-memory store. Portfolio search, regulation search, bid search, web search, and the models are fakes. A model reports a score of 9 only when its requirement, the tender, the whole rubric, and its evidence arrived. `T-AG-20` keeps `VENUE` as a threshold that scores 9, so `CONTENT` and `EXPERIENCE` are still dispatched. `T-AG-8` makes that same threshold score 4; the later models are not assessed. `T-AG-35` walks `VENUE`, `CONTENT`, `HOST`, `MUSIC`, and `EXPERIENCE`, with that same threshold scoring 9, so all five are submitted. The `HOST` and `MUSIC` models passed by `T-AG-20` and `T-AG-8` fail if they are called.

`tests/test_runner.py` covers `T-AG-27`, `T-AG-28`, and `T-AG-29` at `run_once`. The queue, the score handler, and the completion handler are in-memory. `T-AG-27` uses weights 40/30/30 and three scores of 9, so quality is the literal 90.0. `T-AG-29` uses scores 5, 4, and 6, which the pinned bands can produce, and quality is the literal 50.0.

`tests/test_worker.py` covers `T-AG-36`, `T-AG-37`, and `T-AG-38` at `run_forever`. The queue, `run_once`, and `sleep` are fakes. No SQL, Chroma, or model client is opened. The poll interval in these tests is 5 seconds.

`tests/test_assessment_queue.py` covers `T-AG-1`, `T-AG-39`, `T-AG-40`, `T-AG-11`, `T-CMD-19`, `T-AG-43`, and `T-AG-44` against SQL Server. The suite skips when `SOMEE_DB_CONNECTION_STRING` is unset. `T-AG-39` enqueues the venue answer `היכל`. `T-AG-40` seals `900000` on the bid stream. `T-AG-11` claims one pending bid from two connections. `T-AG-43` ages that row past a timeout which still covers every other claim, so the sweep returns that bid alone and the next claim takes it. `T-AG-44` releases against that same kind of timeout immediately after claim.

`tests/test_agent_ports.py` covers `T-AG-45` through `T-AG-54` without Tavily, OpenAI, Ollama, SQL Server, or Chroma. `T-AG-46` posts through a stand-in opener. `T-AG-47` through `T-AG-52` return a stand-in completion. `T-AG-54` passes stand-ins for the queue and the vector store.

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
| Deep Agent | `NFR-AGT-1` … `NFR-AGT-4` | `T-AG-1` … `T-AG-35` |
| Determinism | `NFR-DET-1` | `T-AG-14`, `T-SC-1` … `T-SC-19` |
| Environment | `ENV-1` … `ENV-6` | `T-ENV-1` … `T-ENV-8` |
