# 05 — Agent Specification

**Status:** Derived from `00-overview.md` §6. Satisfies `NFR-AGT-1` … `NFR-AGT-4`, `FR-EVAL-2`, `FR-EVAL-3`, `FR-EVAL-10`.

---

## 1. Process model

A standalone process, `python -m agent.runner`, with no import from `app/controllers/` and no HTTP interface. It shares only the database and the Chroma server with the web process (`NFR-AGT-2`).

**First statement of the entry point**, before any client object exists:

```python
import truststore
truststore.inject_into_ssl()
```

Without it every OpenAI call fails certificate verification on this network (`ENV-1`). This is a functional dependency, not hardening.

---

## 2. How the agent reads and writes

The agent has three channels, and the split is what makes price isolation structural rather than a promise.

| Channel | Direction | Carries |
| --- | --- | --- |
| `rm_bid_for_assessment` read model, via `pyodbc` | read | Work queue and bid content |
| `get_tender_requirements` MCP tool | read | Requirements, weights, threshold flags, approved host list |
| `submit_requirement_score` MCP tool | write | One `RequirementScored` event per call |

`rm_bid_for_assessment` is a purpose-built projection containing `bid_id`, `tender_id`, `concept`, and the per-requirement responses. **It has no price column.** Price lives only in the `PriceOfferSealed` payload on the bid stream, which the agent never replays. So `NFR-AGT-4` holds because the data is absent from the agent's only source, not because the code declines to look — there is nothing to look at.

Writes go exclusively through `submit_requirement_score`. The agent never issues SQL writes and never appends to `Events` directly, which keeps project rule 1 intact.

---

## 3. Trigger

| Mode | Mechanism |
| --- | --- |
| Autonomous | Poll every `AGENT_POLL_SECONDS` (default 60) for tenders in *under evaluation* with unassessed bids |
| On demand | Organizer's "Evaluate Now" sets a pending flag the next poll picks up (`FR-EVAL-10`) |

"Evaluate Now" deliberately does not call the agent over HTTP. The web process stays unaware of the agent's existence, and the demo never waits on a timer.

A bid is claimed before work begins so two poll cycles cannot double-process it. Combined with idempotency in §6, a crash mid-assessment is safe: the next cycle resumes at the first unscored requirement.

---

## 4. Deep Agent structure

A planning orchestrator over five specialist sub-agents (`NFR-AGT-1`). The decomposition is one sub-agent per requirement type, which is the natural fit noted in `00-overview.md` §7.

```
Orchestrator
  ├── plan: fetch requirements, map each to its sub-agent
  ├── dispatch: run sub-agents (independent, parallelizable)
  │     ├── VenueSubAgent        ← also safety + capacity
  │     ├── ContentSubAgent
  │     ├── HostSubAgent
  │     ├── MusicSubAgent
  │     └── ExperienceSubAgent
  ├── collect: submit each score via the MCP tool
  └── finalize: threshold check → quality → terminal event
```

### Orchestrator procedure

1. Claim a bid from `rm_bid_for_assessment`.
2. Call `get_tender_requirements(tender_id)`.
3. For each requirement, retrieve its rubric from the `rubrics` collection, unchunked.
4. Dispatch the matching sub-agent with the requirement, the supplier's response, and the rubric.
5. Call `submit_requirement_score` per result.
6. **Threshold short-circuit:** if a threshold requirement scores below 6, stop dispatching, emit `BidDisqualified` with `failed_requirement_id`, and leave remaining requirements unscored (`FR-EVAL-3`).
7. Otherwise compute quality in deterministic code, apply the floor of 60, and emit `BidDisqualified` or `QualityAssessmentCompleted`.

Step 7 is arithmetic in `app/domain/scoring.py`, not a model call (`NFR-DET-1`).

---

## 5. Sub-agents

Each receives the requirement, the supplier's answer, and the rubric; each returns one structured verdict. None of them sees a price.

| Sub-agent | Vector collections | Tavily verification |
| --- | --- | --- |
| `VENUE` | `regulations` | Official capacity of the named venue; licensing and accessibility obligations at that capacity |
| `CONTENT` | `rubrics`, `bid_concepts` | Whether named performers and works are active and real |
| `HOST` | — | Conflicting engagements for the host on the event date |
| `MUSIC` | `bid_concepts` | Whether named acts are touring or active |
| `EXPERIENCE` | `portfolios` | Corroboration of claimed past productions; reviews and press reports |

### `VENUE` — absorbs safety and capacity

Three checks in one score, per `00-overview.md` §6:

1. Does the declared venue exist and what is its official capacity (Tavily)?
2. Does that capacity satisfy the tender's requirement?
3. Does the plan satisfy the mass-gathering regulations applicable at that capacity (`regulations` collection)?

A safety or licensing shortfall caps the score in the 0–4 band regardless of how well the venue reads, because a non-compliant venue cannot host the event. Folding this in is what let `SAFETY` and `CAPACITY` be dropped as separate types without orphaning the regulations knowledge base.

### `HOST`

The approved host list arrives inside `get_tender_requirements`, so no third tool is needed. A host absent from the list fails the requirement outright — score 0 — since approval is categorical, not a matter of degree. A listed host with a same-date conflict found via Tavily scores in the 3–4 band: approved, but likely unavailable.

### `CONTENT` and `MUSIC`

Comparative rather than factual. Both retrieve past winning concepts from `bid_concepts` so the rubric is applied against precedent instead of the model's taste. Both verify that named performers exist and are active; a fabricated performer scores 0 per §2 of `04-scoring-model.md`.

### `EXPERIENCE`

Verifies each claimed production. The rubric keys off how many claims were corroborated, so an unverifiable claim and a disproved claim land differently — inconclusive reduces the score, disproved sets 0.

---

## 6. Structured output

Every sub-agent returns this shape, enforced by the provider's JSON-schema mode (`NFR-AGT-3`):

```json
{
  "requirement_id": "string",
  "score": 0,
  "justification": "string",
  "sources": ["https://..."],
  "verifications": [
    { "claim": "string", "status": "verified | disproved | inconclusive", "source": "https://..." }
  ]
}
```

Rules:

- `score` is an integer 0–10. A non-integer is a schema violation, not something to round.
- `justification` is written in English, like all stored domain text (`NFR-UI-2`); the Hebrew UI presents it as-is.
- `sources` must be non-empty whenever `verifications` contains a `verified` or `disproved` entry. A factual claim with no source is rejected and retried.
- `temperature = 0` on every call.

### Idempotency and retries

`submit_requirement_score` is idempotent on `(bid_id, requirement_id)` (`02-architecture.md` §7), so a retry after a timeout cannot double-score. On transient API failure a sub-agent retries up to three times with backoff; on persistent failure the bid is released unclaimed and left for a later cycle rather than scored with a guess. A bid is never assessed with a missing requirement score.

---

## 7. Boundaries

What the agent must never do, each traceable to a rule:

| Prohibition | Why |
| --- | --- |
| Read a price | `NFR-AGT-4`; its read model has no price column |
| Compute a price score or final score | `NFR-DET-1`, project rule 2 |
| Emit `PricesRevealed` | Web process only (`02-architecture.md` §3) |
| Select a winner | `FR-EVAL-8`; the human decides |
| Write SQL directly | Project rule 1 |
| Score after `PricesRevealed` | `INV-4` |

---

## 8. Observability

Each cycle logs the bid claimed, each sub-agent dispatched, each score with its latency, and the terminal event. Logs are operational only — the audit trail is the event stream (`NFR-ES-5`), and anything needed to justify a decision belongs in a payload, not a log line.

Token cost is bounded by rubric size, since rubrics are retrieved whole. At five requirements per bid and four bids, one tender is twenty scoring calls plus Tavily lookups — negligible, but worth stating because it is why unchunked rubrics are affordable.
