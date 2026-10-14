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
| `rm_bid_for_assessment` read model, via `pyodbc` | read and claim | Work queue and bid content |
| `get_tender_requirements` MCP tool | read | Requirements, weights, threshold flags, approved host list |
| `submit_requirement_score` MCP tool | write | One `RequirementScored` event per call |

`rm_bid_for_assessment` is a purpose-built projection containing `bid_id`, `tender_id`, `supplier_id`, `concept`, and the per-requirement responses. **It has no price column.** `enqueue_bid_for_assessment` writes those `BidSubmitted` fields only. Price lives only in the `PriceOfferSealed` payload on the bid stream, which the agent never replays. So `NFR-AGT-4` holds because the data is absent from the agent's only source, not because the code declines to look — there is nothing to look at.

Requirement scores go through `submit_requirement_score`. `claim` updates the queue row in its own committed transaction. `QualityAssessmentCompleted` and `BidDisqualified` go through `CompleteAssessment`, which also sets that row to `completed`. The agent does not append to `Events` on its own.

---

## 3. Trigger

| Mode | Mechanism |
| --- | --- |
| Autonomous | Poll every `AGENT_POLL_SECONDS` (default 60) for tenders in *under evaluation* with unassessed bids |
| On demand | Organizer's "Evaluate Now" sets a pending flag the next poll picks up (`FR-EVAL-10`) |

"Evaluate Now" deliberately does not call the agent over HTTP. The web process stays unaware of the agent's existence, and the demo never waits on a timer.

A bid is claimed, and that claim is committed, before work begins, so two poll cycles cannot take the same bid (`T-AG-11`). A crash after the claim leaves the row `claimed`. Releasing it, and resuming at the first unscored requirement, stay with `T-AG-9` and `T-AG-10`.

`run_forever` in `agent/worker.py` is that poll (`T-AG-36`, `T-AG-37`, `T-AG-38`). It calls `run_once`. A return of 1 claims the next bid immediately. A return of 0 sleeps `AGENT_POLL_SECONDS` and polls again. SIGINT and SIGTERM, once `main` starts the loop, only set a flag that is checked before the next claim. The `run_once` already in progress finishes, including its terminal command. `python -m agent.runner` stays the entry point. `main` still prints that the queue is not connected: it does not construct `SqlAssessmentQueue` or the models.

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

`score_bid` in `agent/orchestrator.py` is steps 2–5 for `VENUE`, `CONTENT`, `HOST`, `MUSIC`, and `EXPERIENCE`, and the threshold stop in step 6 (`T-AG-20`, `T-AG-8`, `T-AG-35`). Answers are a map from requirement id to response text. `supplier_id` is an argument, because `evaluate_experience` scopes `search_portfolios` to one supplier and `rm_bid_for_assessment` does not carry it yet. The function calls `get_tender_requirements` and `submit_requirement_score` through the same Python functions the MCP tools wrap. It walks the requirement list in order, loads each rubric whole, and dispatches `evaluate_venue`, `evaluate_content`, `evaluate_host`, `evaluate_music`, or `evaluate_experience`. `approved_hosts` is read from that payload and passed only to `evaluate_host`. A `HOST` requirement whose payload has no `approved_hosts` raises `ValueError` before any score is submitted. An empty list is a real list. A type outside those five raises `ValueError` before any score is submitted. A threshold requirement scoring below 6 is submitted, and later requirements are not dispatched.

`run_once` in `agent/runner.py` is the poll cycle (`T-AG-27`, `T-AG-28`, `T-AG-29`). It claims one `AssessmentWork` from the queue — `bid_id`, `tender_id`, `supplier_id`, and the answers, with no price — calls `score_bid`, then `CompleteAssessment`. A second claim in the same cycle is not taken; the next `run_once` claims nothing when the queue is empty. When a submitted threshold score is below 6, the command is `BidDisqualified` with `reason` `threshold` and `failed_requirement_id`, and `quality_score` is absent. Otherwise quality is `quality` in `app/domain/scoring.py`. Quality below 60 emits `BidDisqualified` with `reason` `quality_floor` and `quality_score`, and `failed_requirement_id` is absent. Quality of 60 or more emits `QualityAssessmentCompleted` with `quality_score` and `requirement_scores`. Resume-after-crash stays later (`T-AG-10`). `SqlCompleteAssessmentHandler` appends the terminal event and projects the assessment read models (`T-CMD-13`, `T-CMD-14`). Ranking stays in the web `RevealPrices` handler.

---

## 5. Sub-agents

Each receives the requirement, the supplier's answer, and the rubric; each returns one structured verdict. None of them sees a price.

| Sub-agent | Vector collections | Tavily verification |
| --- | --- | --- |
| `VENUE` | `regulations` | Official capacity of the named venue; licensing and accessibility obligations at that capacity |
| `CONTENT` | `bid_concepts` | Whether named performers and works are active and real |
| `HOST` | — | Conflicting engagements for the host on the event date |
| `MUSIC` | `bid_concepts` | Whether named acts are touring or active |
| `EXPERIENCE` | `portfolios` | Corroboration of claimed past productions; reviews and press reports |

### `VENUE` — absorbs safety and capacity

Three checks in one score, per `00-overview.md` §6:

1. Does the declared venue exist and what is its official capacity (Tavily)?
2. Does that capacity satisfy the tender's requirement?
3. Does the plan satisfy the mass-gathering regulations applicable at that capacity (`regulations` collection)?

A safety or licensing shortfall caps the score in the 0–4 band regardless of how well the venue reads, because a non-compliant venue cannot host the event. Folding this in is what let `SAFETY` and `CAPACITY` be dropped as separate types without orphaning the regulations knowledge base.

`evaluate_venue` in `agent/subagents/venue.py` is that sub-agent:

```python
evaluate_venue(requirement, answer, rubric, tender, search, web, model) -> RequirementVerdict
```

The orchestrator passes the requirement, the supplier's answer, the whole rubric, and tender context (`name`, `event_date`, `region`). `search` is `SemanticSearchService`. `web` is a search port. `model` proposes the integer at temperature 0 and reports three findings: `capacity_claim` (`verified`, `disproved`, or `inconclusive`), `meets_tender_capacity`, and `regulation_breached`. The function applies `knowledge_base/rubrics/venue.md`'s first-match rule before it returns the §6 verdict: a disproved capacity claim scores 0; a breached retrieved obligation pins the proposal to 0–4; verified capacity below the tender figure, with no breach, pins it to 3–4; an inconclusive claim pins it to 5–6; verified capacity that meets the tender, with no breach, pins it to 7–10. Inside a band, the model's integer is kept. A proposal that is not a real `int` in 0–10 is rejected. That adjustment is the rubric constraint on this one score. Quality, price, and rank stay in `app/domain/scoring.py`.

### `HOST`

The approved host list arrives inside `get_tender_requirements`, so no third tool is needed. Membership is a comparison in `evaluate_host`, not a model opinion: both the extracted name and each list entry are `strip().casefold()`-ed, and equality is exact. A named host absent from the list scores in the 1–2 band. Score 0 is reserved for a requirement where no host was proposed. A listed host with a same-date conflict found via Tavily scores in the 3–4 band: approved, but likely unavailable.

`evaluate_host` in `agent/subagents/host.py` is that sub-agent:

```python
evaluate_host(requirement, answer, rubric, tender, approved_hosts, web, model) -> RequirementVerdict
```

The caller passes the requirement, the supplier's answer, the whole rubric, tender context (`name`, `event_date`, `region`), and `approved_hosts`. There is no vector search. `web` is a search port for same-date engagements. `model` proposes the integer at temperature 0 and reports four findings: `host_name`, `backup_name`, `primary_conflict`, and `backup_conflict`. The function applies `knowledge_base/rubrics/host.md`'s first-match rule before it returns the §6 verdict. The bands this function pins are:

1. No host name after `strip` → score 0.
2. A named host whose normalized name is not in `approved_hosts` → the proposal is pinned to 1–2.
3. The primary host is listed and `primary_conflict` → the proposal is pinned to 3–4.
4. The primary host is listed, the backup's normalized name is listed, and `backup_conflict` is false → the proposal is pinned to 9–10.

Rule 4 does not repeat "no conflict". Rule 3 is first, so a listed backup does not lift a conflicted primary into 9–10. A proposal that is not a real `int` in 0–10 is rejected. Any other finding combination is rejected: the 5–6 and 7–8 bands stay in the rubric until a later slice. That adjustment is the rubric constraint on this one score. Quality, price, and rank stay in `app/domain/scoring.py`.

### `CONTENT`

Comparative rather than factual. Past concepts come from `bid_concepts` so the rubric is applied against precedent instead of the model's taste. Named performers and works are checked for being active and real; a fabricated performer, work, or segment scores 0 per §2 of `04-scoring-model.md`.

`evaluate_content` in `agent/subagents/content.py` is that sub-agent:

```python
evaluate_content(requirement, answer, rubric, tender, search, web, model) -> RequirementVerdict
```

The caller passes the requirement, the supplier's answer, the whole rubric, and tender context (`name`, `event_date`, `region`). `search` is `SemanticSearchService`. `search_bids` is called with no `tender_id`, so precedent is taken from `bid_concepts` across tenders. `web` is a search port. `model` proposes the integer at temperature 0 and reports three findings: `performer_claim` (`verified`, `disproved`, or `inconclusive`), `both_components_sound`, and `substantive_defect`. The function applies `knowledge_base/rubrics/content.md`'s first-match rule before it returns the §6 verdict. The bands this function pins are:

1. `performer_claim == "disproved"` → score 0.
2. `both_components_sound` and `performer_claim == "verified"` → the proposal is pinned to 9–10.
3. `substantive_defect` → the proposal is pinned to 3–4.

`both_components_sound` means the schedule and the concept are both present and relevant to the event. `substantive_defect` means the answer addresses content, but a present component is defective in the way the rubric's 3–4 band describes — a self-contradictory schedule beside a relevant concept, for example. A proposal that is not a real `int` in 0–10 is rejected. Any other finding combination is rejected: the 7–8, 5–6, and 1–2 bands stay in the rubric until a later slice. That adjustment is the rubric constraint on this one score. Quality, price, and rank stay in `app/domain/scoring.py`.

### `MUSIC`

Comparative rather than factual. Past winning concepts come from `bid_concepts` so the rubric is applied against precedent instead of the model's taste. Named acts are checked for being on tour or active; a fabricated act scores 0 per §2 of `04-scoring-model.md`.

`evaluate_music` in `agent/subagents/music.py` is that sub-agent:

```python
evaluate_music(requirement, answer, rubric, tender, search, web, model) -> RequirementVerdict
```

The caller passes the requirement, the supplier's answer, the whole rubric, and tender context (`name`, `event_date`, `region`). `search` is `SemanticSearchService`. `search_bids` is called with no `tender_id`, so precedent is taken from `bid_concepts` across tenders. `web` is a search port. `model` proposes the integer at temperature 0 and reports four findings: `act_claim` (`verified`, `disproved`, or `inconclusive`), `live_offered`, `tender_requires_live`, and `rider_complete`. `rider_complete` means the technical rider covers stage, amplification, and power. The function applies `knowledge_base/rubrics/music.md`'s first-match rule before it returns the §6 verdict. The bands this function pins are:

1. `act_claim == "disproved"` → score 0.
2. `tender_requires_live` and not `live_offered` → the proposal is pinned to 3–4.
3. `act_claim == "verified"` and `rider_complete` and (`live_offered` or not `tender_requires_live`) → the proposal is pinned to 9–10.

Rule 3 requires a verified act, so a disproved act does not match it. Rule 1 is still first. A proposal that is not a real `int` in 0–10 is rejected. Any other finding combination is rejected: the 7–8, 5–6, and 1–2 bands stay in the rubric until a later slice. That adjustment is the rubric constraint on this one score. Quality, price, and rank stay in `app/domain/scoring.py`.

### `EXPERIENCE`

Verifies each claimed production against the supplier's own portfolio and against reviews or reports. The rubric keys off how many outdoor or stadium productions were corroborated. One disproved production scores 0 even when others were verified (`knowledge_base/rubrics/experience.md`).

`evaluate_experience` in `agent/subagents/experience.py` is that sub-agent:

```python
evaluate_experience(requirement, answer, rubric, tender, supplier_id, search, web, model) -> RequirementVerdict
```

The caller passes the requirement, the supplier's answer, the whole rubric, tender context (`name`, `event_date`, `region`), and `supplier_id`. `search` is the portfolio port. `search_portfolios(query, supplier_id)` returns only that supplier's past work; `SemanticSearchService` does not implement the method yet (`02` §6). `web` is a search port. `model` proposes the integer at temperature 0 and reports three findings: `any_disproved`, `verified_outdoor_count`, and `outdoor_claimed`. The function applies the rubric's first-match rule before it returns the §6 verdict. The bands this function pins are:

1. `any_disproved` → score 0.
2. `verified_outdoor_count >= 3` → the proposal is pinned to 9–10.
3. `outdoor_claimed` and `verified_outdoor_count == 0` → the proposal is pinned to 5–6.

`verified_outdoor_count` is how many claimed outdoor or stadium productions were independently verified. `outdoor_claimed` means the answer claims at least one. Rule 1 is first, so three verified outdoor shows plus one disproved production still score 0. A proposal that is not a real `int` in 0–10 is rejected, as is a `verified_outdoor_count` that is not a real `int`. Any other finding combination is rejected: the 7–8, 3–4, and 1–2 bands stay in the rubric until a later slice. That adjustment is the rubric constraint on this one score. Quality, price, and rank stay in `app/domain/scoring.py`.

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
