---
name: requirement-evaluator
description: Add a new requirement type to BidStage — rubric, evaluation sub-agent, orchestrator wiring, specs and tests. Use when adding or extending a requirement category such as SAFETY, CATERING or BROADCAST, or when asked to add an evaluation sub-agent.
---

# Adding a Requirement Type

The requirement type taxonomy is **deliberately closed** at five: `VENUE`, `CONTENT`, `HOST`, `MUSIC`, `EXPERIENCE`. Each type binds to exactly one rubric and one sub-agent, so an unregistered type has nothing to score it. Adding a sixth is a spec change with code consequences, not a runtime configuration — this procedure is that change.

Work the steps in order. Steps 1–2 are specification work and come before any code, per SDAD.

## 1. Specify before coding

Add, in this order:

- `specs/00-overview.md` §3 — the new type in the implemented list.
- `specs/01-requirements.md` — requirement IDs for the behaviour, and a note if the type is ever usable as a threshold requirement.
- `specs/03-domain-events.md` — only if a new event is needed. Usually none is: scoring reuses `RequirementScored`.
- `specs/05-agent-spec.md` §5 — a row in the sub-agent table stating which collections and which Tavily checks it uses.
- `specs/06-tests.md` — test IDs, including at least one disqualification case.

If the new type duplicates an existing one's checks, fold it into that type's rubric instead of adding a type. `SAFETY` and `CAPACITY` were folded into `VENUE` for exactly this reason.

## 2. Register the type

Add the constant to the requirement-type enum and keep it closed: unknown values are rejected, never defaulted. Weights are per tender and must still sum to 100%.

## 3. Write the rubric

Create `knowledge_base/rubrics/<type>.md`. It is embedded **unchunked** into the `rubrics` collection — one document per type. A fragmented rubric can return half a scoring scale and silently degrades score consistency.

Scores are integers **0–10**, matching `specs/04-scoring-model.md` §2. Do not invent a 0–100 scale: 0–100 is the *aggregate quality score*, computed from these weighted 0–10 values.

| Band | Meaning |
| --- | --- |
| 9–10 | Fully meets the requirement, evidence independently verified |
| 7–8 | Meets it; evidence partially verified |
| 5–6 | Broadly meets it with a material gap or unverifiable claim |
| 3–4 | Falls short on a substantive element |
| 1–2 | Barely addresses the requirement |
| 0 | Not addressed, or a claim found to be false |

Add type-specific anchors under each band so two runs agree. A verified-false claim is 0 regardless of presentation quality.

A threshold requirement must score **6 or above** to pass; below that the bid is disqualified and remaining requirements go unscored.

## 4. Choose a verification strategy

Pick deliberately and record it in `05-agent-spec.md`:

- **Chroma RAG** — compare against `regulations`, past winning `bid_concepts`, or supplier `portfolios`.
- **Tavily MCP** — verify external facts: official figures, availability, corroboration of claims.
- **SQL reference table** — check a closed approved list. Deliver it inside `get_tender_requirements`, as `HOST` does with `approved_hosts`. Do **not** add a third MCP tool; the project is fixed at two.

## 5. Implement the sub-agent

Create `agent/subagents/<type>.py`. Note the directory: `agent/subagents/`, not `agent/evaluators/`.

Requirements:

- Return the structured shape from `specs/05-agent-spec.md` §6: `requirement_id`, `score`, `justification`, `sources`, `verifications`. Enforce it with OpenAI structured outputs at `temperature = 0`.
- `score` is an integer 0–10. A non-integer is a schema violation, not something to round.
- `sources` must be non-empty whenever a factual claim was verified or disproved.
- Retrieve the rubric whole; never summarize it into the prompt.
- Compute **no** aggregate: quality, price and final scores belong to `app/domain/scoring.py` (`NFR-DET-1`).
- Receive **no** price. The sub-agent's input comes from `rm_bid_for_assessment`, which has no price column — never add one, and never pass a price into a prompt.

## 6. Wire the result

Persist through the existing MCP tool, which appends a **`RequirementScored`** event:

```python
submit_requirement_score(
    bid_id=bid_id,
    requirement_id=requirement.id,
    score=verdict.score,
    justification=verdict.justification,
    sources=verdict.sources,
)
```

The event is `RequirementScored` — not `RequirementEvaluated`, which does not exist in the catalogue. Event names are append-only; never invent a parallel name for an existing concept.

The tool is idempotent on `(bid_id, requirement_id)`, so a retry cannot double-score. Register the sub-agent in the orchestrator's type-to-sub-agent map so the dispatch in `05-agent-spec.md` §4 picks it up.

## 7. Reseed and test

Re-run the seed script so the new rubric is embedded, then add seed bid data that exercises both a passing and a disqualifying score for the new type. Verify the rubric retrieves as one whole document.
