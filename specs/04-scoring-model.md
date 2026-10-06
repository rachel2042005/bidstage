# 04 — Scoring Model

**Status:** Derived from `00-overview.md` §4. Satisfies `FR-EVAL-3` … `FR-EVAL-9`, `NFR-DET-1`, `NFR-AGT-3`.

This is the part of the system a reviewer will probe hardest, so every value here is pinned and every example is arithmetically checkable.

---

## 1. Division of labour

| Computed by | What |
| --- | --- |
| Agent (LLM) | A 0–10 score per requirement, with justification and sources |
| Deterministic Python | Quality aggregation, threshold checks, floor check, price score, final score, ranking |

The LLM contributes **only** the per-requirement integers. Every arithmetic step is ordinary code (`NFR-DET-1`), which is both a project rule and the reason the model below is reproducible.

---

## 2. Rubric

One rubric per requirement type, stored unchunked in the `rubrics` collection (`NFR-VEC-3`). Each defines what each band means, so the same bid scores the same way twice.

| Band | Meaning |
| --- | --- |
| 9–10 | Fully meets the requirement with independently verified evidence |
| 7–8 | Meets the requirement; evidence partially verified |
| 5–6 | Broadly meets it with a material gap or unverifiable claim |
| 3–4 | Falls short on a substantive element |
| 1–2 | Barely addresses the requirement |
| 0 | Not addressed, or a claim found to be false |

Each rubric adds type-specific anchors — for `VENUE`, whether the official capacity was located and whether it satisfies both the tender figure and the regulatory limit; for `EXPERIENCE`, how many claimed productions were corroborated. A verified-false claim scores 0 regardless of presentation quality, which is what makes Tavily verification consequential rather than decorative.

Every `RequirementScored` event records `rubric_version`, so a score remains explainable after a rubric changes (`03-domain-events.md` §6).

---

## 3. Quality score

\[
\text{Quality} = \frac{\sum_i (w_i \times s_i)}{10 \times \sum_i w_i} \times 100
\]

With weights in percent summing to 100 (`FR-ENT-3`), this reduces to:

\[
\text{Quality} = \frac{\sum_i (w_i \times s_i)}{10}
\]

Range 0–100. Weights are validated at publication, so the denominator is never zero and never varies between bids on one tender.

---

## 4. Disqualification

Two independent gates, checked in this order:

**Threshold requirements.** A requirement with `is_threshold = true` must score **6 or above**. Below that the bid is disqualified immediately, remaining requirements are not scored, and `BidDisqualified` carries `failed_requirement_id`.

> The pass mark of 6 is the one value not fixed in `00-overview.md`; it is set here as the floor of the "broadly meets it" band so that a material gap still passes a threshold but a substantive shortfall does not. Confirm it before implementation.

**Quality floor.** A bid scoring below **60** overall is disqualified even with every threshold passed (`FR-EVAL-4`). Fixed system-wide and not editable per tender, so it cannot be moved once bids are visible.

A disqualified bid is excluded from ranking and from the price pool, but remains visible with its reason (`FR-TBL-3`, `FR-DET-4`).

---

## 5. Price score

Computed only after `PricesRevealed`.

\[
\text{Price score} = \frac{P_{\min}^{\text{qualified}}}{P_{\text{bid}}} \times 100
\]

\(P_{\min}^{\text{qualified}}\) is the lowest price among **qualified** bids only — those passing both gates in §4. A disqualified bid never influences another bid's price score, even when its price was lowest overall. Range is \((0, 100]\), reaching 100 only for the cheapest qualified bid.

---

## 6. Final score

\[
\text{Final} = \alpha \times \text{Quality} + (1 - \alpha) \times \text{Price score}
\]

α is per tender, set by the organizer, default **0.6**. Range 0–100.

**Precision.** Intermediate values are carried at full floating-point precision; only presentation rounds, to one decimal. Nothing rounds before the final multiplication, so the displayed ranking always matches the computed one.

**Ties.** Equal final scores break by higher quality, then by earlier submission timestamp. Ranking is therefore total and stable.

---

## 7. Worked example

Tender weights: `VENUE` 25%, `CONTENT` 25%, `HOST` 15%, `MUSIC` 15%, `EXPERIENCE` 20%. α = 0.6. No threshold failures.

### Per-requirement scores

| Requirement | Weight | Supplier A | A weighted | Supplier B | B weighted |
| --- | --- | --- | --- | --- | --- |
| `VENUE` | 25 | 9 | 225 | 8 | 200 |
| `CONTENT` | 25 | 9 | 225 | 7 | 175 |
| `HOST` | 15 | 7 | 105 | 7 | 105 |
| `MUSIC` | 15 | 7 | 105 | 8 | 120 |
| `EXPERIENCE` | 20 | 8 | 160 | 7 | 140 |
| **Total** | **100** | | **820** | | **740** |

Quality A = 820 / 10 = **82.0**  ·  Quality B = 740 / 10 = **74.0**

Both clear the floor of 60, so both are qualified and both enter the price pool.

### Price and final

Lowest qualified price = ₪700,000.

| Bid | Quality | Price | Price score | Final |
| --- | --- | --- | --- | --- |
| Supplier A | 82.0 | ₪900,000 | 700/900 × 100 = **77.8** | 0.6(82.0) + 0.4(77.8) = **80.3** |
| Supplier B | 74.0 | ₪700,000 | **100.0** | 0.6(74.0) + 0.4(100.0) = **84.4** |

**Supplier B wins on a lower quality score.** This is the demonstration case: it proves the weighting genuinely decides outcomes rather than rubber-stamping the best quality.

### Variant: the cheapest bid is disqualified

Add Supplier C at ₪500,000 who scores 4 on a threshold `VENUE` requirement. C is disqualified at the first gate and never scored further. \(P_{\min}^{\text{qualified}}\) stays ₪700,000, so A and B keep the scores above. Had C's price entered the pool, A would have dropped to 0.6(82) + 0.4(55.6) = 71.4 and B to 0.6(74) + 0.4(71.4) = 72.9 — both distorted by a bid that was never eligible. This is the concrete reason for the qualified-only boundary.

---

## 8. Degenerate cases

| Case | Behaviour |
| --- | --- |
| No qualified bids | No \(P_{\min}^{\text{qualified}}\) exists, so no ranking. `TenderFailed`; `PricesRevealed` is never emitted (`INV-7`) |
| Exactly one qualified bid | Price score 100 by definition; final = 0.6 × quality + 40. Still requires explicit organizer confirmation (`FR-EVAL-8`) |
| All qualified bids priced equally | Every price score is 100; ranking collapses to quality order |
| Zero bids submitted | `TenderClosedForBids` with `bid_count = 0`, then `TenderFailed` |
| α = 1 | Pure quality; price score computed and displayed but not weighted |
| α = 0 | Pure price; quality still gates via thresholds and the floor |

α = 0 is worth noting: the floor still applies, so a cheap unqualified bid cannot win. Quality is never fully bypassed.

---

## 9. Override recomputation

When an organizer overrides a score (`FR-EVAL-7`):

1. `ScoreOverridden` is appended, preserving `old_score` and the mandatory justification.
2. Quality is recomputed from the effective scores, where an override supersedes the agent's score for that requirement.
3. Both gates in §4 are re-evaluated — an override can disqualify a previously qualified bid, or rescue a disqualified one.
4. Because the qualified set may have changed, \(P_{\min}^{\text{qualified}}\) is recomputed, which can change **every** bid's price score, not only the overridden bid's.
5. A fresh `FinalRankingCalculated` is appended (`INV-8`).

Step 4 is the easy mistake: an override on one bid can reorder the others. The agent's original score is never overwritten; both values remain visible (`FR-DET-5`).

---

## 10. Reproducibility

`NFR-AGT-3` requires comparable scores across runs. Controls:

| Control | Rationale |
| --- | --- |
| `temperature = 0` | Removes sampling variance |
| Structured output against a fixed JSON schema | No free-form parsing, no drift in shape |
| Banded rubric with explicit anchors | Scores are matched to descriptions, not invented |
| Rubrics retrieved unchunked | A partial rubric would mean a partial scale |
| Integer scores only | No false precision to wobble |
| `rubric_version` recorded per score | Variance across runs is attributable |

The presentation should include a repeat run on one bid to show the scores land in the same place. Identical output is not guaranteed and should not be claimed; stability within a band is the honest claim, and the rubric is what delivers it.
