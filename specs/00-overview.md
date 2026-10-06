# BidStage — Project Overview

**Status:** Draft · Foundation spec · Round 1 decisions settled
**Approach:** SDAD (Specification-Driven Application Development) — specifications are written and agreed before any code is produced.
**Team:** Pair (2 students) — scope per §11.

---

## 1. Purpose

BidStage is a web-based information system that manages the tender process for selecting a production supplier for cultural and sports events, such as an awards ceremony, a concert or a sports final.

An event organizer publishes a tender defining the event's requirements — venue location and capacity, program content, a host chosen from an approved list, musical performances, and so on. Each requirement carries a weight, and some are marked as mandatory threshold conditions. Suppliers search for open tenders and submit bids that respond to every requirement, together with a separate sealed price offer.

An autonomous AI agent, built on a Deep Agent framework and running as an independent process, evaluates each bid requirement by requirement. It verifies supplier claims through web search and compares bids against scoring rubrics, regulations and past evaluations held in a vector database. For each requirement it produces a score with a written justification and sources, then calculates an overall quality score. Only after the quality evaluation is complete does the system reveal prices and compute a final ranking weighting quality against price. The organizer reviews the results and makes the final decision on the winner.

### Why this domain fits the architecture

A tender is a natural fit for the reference problem: a tender is an open position, a supplier is a candidate, and a bid is the résumé. The advantage of the tender framing is that the agent's role is structured and transparent — it produces a separate score per requirement rather than one vague overall match score. Transparency and auditability are genuine business requirements in public tendering, which gives Event Sourcing real business justification rather than only a technical one. This point should be emphasized when defending the project.

---

## 2. Actors

| Actor | Responsibilities |
| --- | --- |
| **Organizer** (tender owner) | Publishes tenders, defines requirements and weights, sets the quality/price ratio, reviews assessments, overrides scores with justification, selects the winner |
| **Supplier** (producer) | Searches open tenders, submits a bid with a sealed price offer, tracks bid status |

Exactly two roles are implemented, both with full authentication. The **administrator** role from the original concept is **dropped** under pair scope (§11). Its responsibility for maintaining the approved host list is therefore unassigned — see §13.

---

## 3. Domain Entities

### Tender
Event name, type (culture or sports), event date, region, estimated budget, submission deadline, and the quality-versus-price weighting ratio (α).

### Requirement
Belongs to a tender. Has a type, a weight in percent, and a flag marking it as a mandatory threshold condition whose failure disqualifies the bid.

Implemented requirement types — exactly five, matching the §4 worked example: `VENUE`, `CONTENT`, `HOST`, `MUSIC`, `EXPERIENCE`.

The `CAPACITY` and `SAFETY` types from the original concept are **not** implemented. See §13 for the consequences, which reach into §6 and §7.

The sum of requirement weights within a tender must equal 100%.

### Bid
Submitted by a supplier against a tender. Contains a detailed response to every requirement, a free-text concept description, and a price offer that is stored sealed.

### Assessment
Produced by the agent for a bid. Holds, per requirement, a score, a written justification and the sources consulted; plus the resulting overall quality score.

---

## 4. Scoring Model

This is the part that will stand out in the presentation, so it is specified precisely.

### Quality score

The agent scores each requirement from 0 to 10 against a rubric stored in the vector database. The quality score is the weighted sum, normalized to 100:

\[
\text{Quality} = \frac{\sum_i (w_i \times s_i)}{\sum_i w_i \times 10} \times 100
\]

where \(w_i\) is the requirement weight and \(s_i\) is its 0–10 score.

### Price score

Computed in ordinary deterministic code, never by the agent, using the standard tender formula:

\[
\text{Price score} = \frac{\text{lowest qualified price}}{\text{bid price}} \times 100
\]

The numerator is the lowest price among **qualified bids only** — those that passed every mandatory threshold requirement *and* met the quality floor. A disqualified bid never influences another bid's price score, even if its price was the lowest overall.

### Final score

\[
\text{Final} = \alpha \times \text{Quality} + (1 - \alpha) \times \text{Price score}
\]

α is set by the organizer per tender, defaulting to **0.6** — 60% quality, 40% price.

### Three governing rules

1. **Minimum quality floor.** Fixed system-wide at **60** and not editable per tender, so it cannot be moved after bids are visible. A bid that does not clear it is disqualified before prices are unsealed.
2. **Double envelope.** The price is held sealed and is revealed only after quality assessment is complete, as in real tenders. The agent is therefore never influenced by price, and Event Sourcing makes the exact moment of reveal provable.
3. **Human decides.** The agent recommends; the organizer chooses. The organizer may override any agent score with a justification, and the override is stored as an event. Manual override is **in scope** at pair size — it is one command and one event, and it is the concrete evidence for this rule.

### Degenerate bid counts

- **No qualified bids.** The tender enters the `Failed` state (§5) and emits `TenderFailed`. No winner.
- **Exactly one qualified bid.** It is by definition the lowest qualified price and scores 100 on price. The ranking still requires explicit organizer confirmation rather than auto-awarding.

### Worked example

Weights: venue 25%, content 25%, host 15%, music 15%, experience 20%. Weighting α = 60% quality, 40% price.

| Bid | Quality | Price | Price score | Final score |
| --- | --- | --- | --- | --- |
| Supplier A | 82 | ₪900,000 | 77.8 | 80.3 |
| Supplier B | 74 | ₪700,000 | 100 | 84.4 |

Supplier B wins despite lower quality. This case should be part of the seed data, because it demonstrates that the weighting genuinely affects the outcome.

---

## 5. Tender Lifecycle

```
Open for bids → Under evaluation → Decided
                      │
                      ├──────────→ Failed     (no bid cleared the quality floor)
                      │
                      └──────────→ Cancelled  (withdrawn by the organizer)
```

The agent begins work only once a tender moves to *under evaluation* — that is, after the submission deadline has passed and the tender is closed for bids.

**Failed** is a distinct terminal state, not a null result. If no bid passes both the threshold requirements and the quality floor there is no lowest qualified price, so no ranking can be computed; the tender emits `TenderFailed` and ends with no winner.

---

## 6. The Evaluation Agent

The agent runs as an independent process, built on a Deep Agent framework. It polls periodically for tenders that have closed for bids, and for each bid it:

1. Fetches the requirements, weights and threshold flags through the project's own tool, and retrieves the rubric for each requirement type from the vector database.
2. Dispatches a sub-agent per requirement. The natural decomposition for a Deep Agent is one sub-agent per requirement type:

   | Requirement | Sub-agent behaviour |
   | --- | --- |
   | Venue | Looks up the venue's official capacity via web search and checks it against the supplier's declaration and the tender requirement. **Also absorbs the safety and capacity checks**: retrieves the mass-gathering regulations for the declared capacity from the vector store and verifies the plan against them |
   | Host | Checks the host against the approved list, which arrives in the `get_tender_requirements` payload, and searches for conflicting engagements on the event date |
   | Content / Music | Evaluates the concept against the rubric and against past winning bids retrieved from the vector database, and verifies the named performers are active |
   | Experience | Verifies events the supplier claims to have produced, and searches for reviews or reports about them |
   
There is no separate safety sub-agent; those checks live in the venue sub-agent above, so the regulations stay in the knowledge base without needing a sixth requirement type.

### Trigger

The agent polls every **60 seconds** for tenders that have closed for bids. The organizer also has an explicit **"Evaluate Now"** action in the UI, so a demo never waits on a timer.

3. Stores a score and justification per requirement, disqualifies the bid if a threshold requirement failed, and finally computes the quality score.

Once every bid is assessed, the **server** — not the agent — reveals the prices and computes the final ranking.

### Score consistency

An LLM may score the same bid differently across runs. Mitigations: a detailed rubric, structured JSON output, and low temperature. Demonstrating in the presentation that a repeat run yields a comparable score will make a strong impression.

---

## 7. Architecture

Flask application, organized per MVC and CQRS, persisting to a cloud database via Event Sourcing. External services are integrated through MCP.

### Technology stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Web framework | Flask | MVC + CQRS layering |
| UI | Bootstrap 5, Hebrew, full RTL | Code and identifiers in English |
| Event store + read models | SQL Server Express on Somee.com | Free tier: 30 MB data, 30 MB log. Demo-scale events are ~40 KB per tender, so the cap is not binding — but embeddings must never go here |
| DB driver | `pyodbc` via ODBC Driver 17 | Driver confirmed present on the dev machine |
| App hosting | Local only, pointing at the Somee database | Somee serves ASP.NET / .NET Core only and cannot host Flask |
| Read models | Projected **synchronously** inside command handlers | No eventual consistency, no demo lag |
| Vector store | Chroma in **server mode**, `chroma run --path ./chroma-data --port 8000` | Both Flask and the agent connect via `HttpClient`; embedded mode is unsafe across processes |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensions | Multilingual, so Hebrew bid text is indexed natively |
| Outbound TLS | `truststore.inject_into_ssl()` at process start | **Mandatory.** Without it the OpenAI API is unreachable on this network — see §13 |
| LLM | OpenAI, structured outputs / JSON schema | For repeatable rubric scoring |
| Agent framework | `deepagents` (LangGraph), independent background process | One sub-agent per requirement type |
| Own MCP tools | FastMCP / MCP SDK | Two tools; see below |
| External MCP | Tavily, Gmail | |
| Python | 3.13 in `.venv` | |

### Commands

`PublishTender` · `AddRequirement` · `SubmitBid` · `WithdrawBid` · `CloseTender` · `OverrideScore` · `SelectWinner`

### Queries

`SearchTenders` · `GetTenderDetails` · `GetBidComparison` · `GetDashboardStats` · `GetBidAssessment`

Every query reads from a dedicated read model — for example a dashboard statistics table kept up to date from the event stream. Projections run synchronously within the command handler that appended the event.

### Event store schema

A single append-only table:

| Column | Purpose |
| --- | --- |
| `StreamId` | Aggregate identity (tender or bid) |
| `version` | Monotonic position within the stream |
| `event_type` | Discriminator |
| `event_data` | Event body |
| `metadata` | Cross-cutting context: actor, role, correlation id |
| `created_at` | Timestamp, defaulted by the database |
| `event_id` | Client-generated UUID; idempotency key |
| `global_seq` | Total append order across streams, for projection rebuilds |

`UNIQUE (StreamId, Version)` is the concurrency control: two writers racing on the same aggregate collide on the constraint, giving optimistic concurrency without extra machinery. Nothing in the system issues an `UPDATE` against this table.

### Domain events

The event stream tells the story of the tender from start to finish:

```
TenderCreated
  → RequirementAdded (per requirement)
  → TenderPublished
  → BidSubmitted + PriceOfferSealed
  → TenderClosedForBids
  → RequirementScored (per requirement, per bid)
  → BidDisqualified | QualityAssessmentCompleted
  → PricesRevealed
  → FinalRankingCalculated
  → ScoreOverridden (optional)
  → WinnerSelected | TenderFailed
```

`TenderFailed` terminates the stream when no bid qualified (§4). `PricesRevealed` is never emitted in that case, which is exactly the property the double envelope is meant to make provable.

### Vector database content

Four collections, kept separate so no query needs metadata filtering:

| Collection | Content | Chunking |
| --- | --- | --- |
| `bid_concepts` | Concepts, artistic programs, content descriptions | By paragraph, ~300–500 tokens |
| `portfolios` | Events each supplier has previously produced | By paragraph, ~300–500 tokens |
| `rubrics` | One scoring rubric per requirement type | **Unchunked** — one document per type |
| `regulations` | Mass-gathering rules: safety, licensing, accessibility | By section, ~500–800 tokens with overlap |

Rubrics are deliberately never chunked: a rubric retrieved in fragments can return half a scoring scale, which would undermine the score consistency the system depends on.

Semantic search enables queries such as *"find bids that include a tribute segment"* or *"suppliers with experience in open-stadium events"*.

### External MCP services

- **Tavily** — web search for the agent's verification work.
- **Gmail** — bid receipt confirmation to the supplier, tender closure notice, and winner/non-winner notifications.

### Own MCP tools

| Tool | Purpose |
| --- | --- |
| `get_tender_requirements(tender_id)` | Returns requirements, weights and threshold flags to the agent. For `HOST` requirements it also returns the approved host list, read from a seeded reference table — this is why no third tool is needed |
| `submit_requirement_score(bid_id, requirement_id, score, justification, sources)` | Persists a requirement score as an event |

Scope is fixed at **two** own tools. The optional third tool `check_host_list(name)` is **dropped**, which leaves the `HOST` sub-agent without a way to verify the approved list — see §13.

---

## 8. Functional Requirements Coverage

| Requirement | Implementation |
| --- | --- |
| 2–3. Users and authentication | Login with role-based permissions: organizer, supplier, administrator |
| 4.1 Search | Suppliers search tenders by type, date and region, plus semantic search; organizers search within bids |
| 4.2 Detail view | Tender page listing all requirements; bid page showing score, justification and agent-found sources per requirement |
| 4.3 Table | Bid comparison table, sortable by quality, price and final score |
| 4.4 Dashboard | Open tenders by status (open for bids, under evaluation, decided, cancelled), bid count per tender, and a countdown to each deadline |
| 4.5 Data entry | Publishing a tender with its requirements; submitting a bid |
| 5. Agent in a separate process | The evaluation agent — see §6 |

## 9. Non-Functional Requirements Coverage

| Requirement | Implementation |
| --- | --- |
| 6. Vector database | Bid content, supplier portfolios and knowledge base — see §7 |
| 7. CQRS | Commands and queries as listed in §7, queries served from dedicated read models |
| 8. Event Sourcing | Event stream as listed in §7; auditability is a real business requirement here |
| 9. External MCP | Tavily and Gmail |
| 10. Own tools | `get_tender_requirements`, `submit_requirement_score`, optional `check_host_list` |
| 11. Deep Agent | One sub-agent per requirement type |

---

## 10. Project Deliverables (course sections 1–3)

### Specification documents

Numbered convention, all under `specs/`:

| Document | Contents |
| --- | --- |
| `00-overview.md` | This document |
| `01-requirements.md` | Functional and non-functional requirements in full |
| `02-architecture.md` | MVC, CQRS and Event Sourcing structure; module layout |
| `03-domain-events.md` | Event catalogue with payloads and invariants |
| `04-scoring-model.md` | Scoring formulas, rubrics, thresholds, worked examples |
| `05-agent-spec.md` | Agent and sub-agent specifications, tools, prompts |
| `06-tests.md` | Test plan and scenarios |

### Skill we wrote

**`requirement-evaluator`** — installed at `.cursor/skills/requirement-evaluator/SKILL.md`.

It is the documented procedure for adding a requirement type: specify first, register the type in the closed enum, write an unchunked 0–10 rubric, choose a verification strategy (Chroma RAG, Tavily, or a seeded SQL reference list), implement the sub-agent under `agent/subagents/`, persist via `submit_requirement_score` as a `RequirementScored` event, then reseed and test.

Because the type taxonomy is closed at five (§3), adding a sixth is a deliberate change touching a rubric, a sub-agent, specs and tests. Encoding that sequence as a skill is what stops it being done partially. The skill also records the three constraints easiest to get wrong: scores are 0–10 and not 0–100, the directory is `agent/subagents/`, and the event is `RequirementScored` — no parallel event name for an existing concept.

### Skill from skills.sh

Two, both from `mattpocock/skills`, installed by direct download (see §13):

| Skill | Location | Use |
| --- | --- | --- |
| `grill-me` + `grilling` | `.cursor/skills/` | Stress-tested this specification across three review rounds before any code was written |
| `tdd` | `.cursor/skills/tdd/` | Red–green discipline for implementation phases |

`tdd` names *horizontal slicing* — writing all tests before any implementation — as an anti-pattern. `06-tests.md` is deliberately written up front as an SDAD deliverable, so during implementation it is the catalogue of what must eventually pass, not a batch to write first: take one test, make it pass, move on.

### Rules

- State changes occur only through a Command that emits an event, never a direct `UPDATE`.
- The price score is computed only in deterministic code, never by an LLM.

---

## 11. Scope — Settled

Pair scope: **two roles** (organizer, supplier) with full authentication, **five requirement types**, administrator dropped, **two** own MCP tools.

The original pair option called for four requirement types; five were kept instead so the §4 worked example and its weights stay coherent. Whether manual score override (§4, rule 3) remains in scope at pair size is still open — see §13.

---

## 12. Seed Data

One or two tenders with three to four contrasting bids: one excellent and expensive, one cheap and mediocre, and one disqualified on a threshold requirement. This exercises every scenario in a demo.

---

## 13. Decisions and Open Questions

### Settled

**Round 1** — team size (pair), scope (§11), event store (SQL Server Express on Somee), LLM provider (OpenAI), agent framework (`deepagents`), α default (0.6, per tender) and quality floor (60, fixed), the qualified-bids price boundary (§4), localization (§7), document naming (§10).

**Round 2** — stay on Somee with a seed/rebuild script, Chroma for vectors, Flask hosted locally, safety and capacity folded into `VENUE` (§6), approved host list seeded in SQL Server and delivered via `get_tender_requirements`, single `Events` table with `UNIQUE (StreamId, Version)`, synchronous projections, `TenderFailed` for the no-qualified-bid case, manual override kept in scope, and a 60-second poll plus an explicit "Evaluate Now" trigger.

### Environment constraints — verified on this machine

1. **The OpenAI API is unreachable with Python's default trust store.** The Netspark filtering proxy re-signs `api.openai.com`, and OpenSSL rejects its certificate with `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier`. Calling `truststore.inject_into_ssl()` before any client is constructed resolves it by using the Windows trust store, which does trust Netspark. Verified: the same handshake fails on certifi and succeeds through `truststore`. This is a hard runtime dependency, not an optimization.
2. **PyPI is *not* intercepted**, so `pip install` works normally. Only certain domains are filtered.
3. **`npm` is intercepted** and fails with `SELF_SIGNED_CERT_IN_CHAIN`; `NODE_EXTRA_CA_CERTS` would be needed if npm tooling is ever required.
4. **Somee deletes idle free databases after 30 days** of no queries, and caps at 30 MB data / 30 MB log / 150 MB disk / 5 GB transfer.
5. **ODBC Driver 17 for SQL Server is installed**, so `pyodbc` needs no additional setup.
6. **Chroma is not process-safe.** Its own documentation states that concurrent writers sharing one local path risk SQLite locks and corruption, and there is a known bug where a second `PersistentClient` on the same directory blocks for roughly 16 minutes. Because the Flask app *and* the separate agent process both need the vector store, embedded mode is not viable as specified — see open question 1.

**Round 3** — Chroma in server mode via `HttpClient`, OpenAI `text-embedding-3-small` at 1536 dimensions, four collections, the chunking rules in §7, and `chroma-data/` gitignored and rebuilt by the seed script.

On the cloud question: the syllabus mandates a cloud database for the **relational event store** only, which Somee satisfies. The vector requirement asks that data be embedded into a vector database for semantic search, with no hosting constraint. A local Chroma in server mode behind a thin interface is therefore compliant. The interface is specified in `02-architecture.md` so the store can be swapped by configuration if that reading ever changes.

### Still open

Nothing. The design tree has been fully walked across three rounds of review; every decision above is recorded rather than assumed. Specification documents `01`–`06` are derived from this one.
