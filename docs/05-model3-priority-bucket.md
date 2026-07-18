# Model 3 — Priority Bucket Model

## What it does

Groups vendors into priority buckets and decides how much each bucket receives when funds are short.

Finance doesn't think in "P0/P1" — they think in three plain categories, and the code maps those to buckets:

| Finance-facing category | Bucket | Definition | Assignment |
|---|---|---|---|
| **Must Pay** | P0 | Rent, other fixed/non-negotiable commitments | Manually tagged by Finance |
| **Commitment** | P1 | Installment plans | Manually tagged by Finance |
| **Normal** (the default) | P2 | Score above 70 | Computed from Model 1's weighted score |
| **Normal** (the default) | P3 | Score between 50 and 70 | Computed from Model 1's weighted score |
| **Normal** (the default) | P4 | Score below 50 | Computed from Model 1's weighted score |

**"Must Pay" and "Commitment" are set by Finance manually, then persisted** (stored so they don't need to be re-entered every cycle) — this is not something the system infers automatically. Every vendor defaults to "Normal" until Finance explicitly marks them Must Pay or Commitment; only "Normal" vendors get bucketed into P2/P3/P4 by score. This three-way category (`MUST_PAY` / `COMMITMENT` / `NORMAL`) should be one shared enum, defined once (see CLAUDE.md's no-hardcoding rule) — the UI shows Finance the plain labels, the code only ever compares against the enum.

## Funding assumption — important

**P0 and P1 are always assumed fully funded.** Per Finance, cash shortfalls only ever affect P2/P3/P4. Do not write logic that reduces P0 or P1 allocations under a shortfall — if a scenario arises where even P0/P1 can't be fully funded, that's an escalation case to flag explicitly, not something to silently shrink.

**"Fully funded" for P0/P1 means the same `required_amount()` Model 2 already computes** (`04-model2-min-funds-advisory.md`: last month's Payable for Must Pay, outstanding ÷ `commitment_months` for Commitment) — confirmed, so this doesn't drift into a second, different definition of "fully funded" between the two models. Reuse that function directly rather than reimplementing it.

## Shortfall allocation — confirmed design

**Superseded the original "P2 to 100%, then P3, then P4" all-or-nothing waterfall.** The problem with that rule: it routinely left P3 and P4 vendors with zero, even when there was some money that could reasonably have reached them. The confirmed replacement spreads a shortfall across all three buckets instead of concentrating it entirely on the lowest-priority ones.

### These caps only apply when funds are actually short — confirmed, stated explicitly

**If the funds remaining after P0/P1 are enough to fully cover P2 + P3 + P4's combined 100% need, skip the percentage-target mechanism entirely and just pay every P2/P3/P4 vendor in full.** The 75/50/40 caps exist only to decide how a genuine shortfall gets spread across the three buckets — they are not a default reduction applied every cycle regardless of funds. (Note: processing the percentage-target steps below and then topping up the leftover toward 100% produces this exact same "everyone gets 100%" result mathematically when funds are sufficient — but implement the upfront check anyway; don't rely on the two-step math to arrive there implicitly, since that's an easy place for an off-by-one or rounding bug to quietly leave someone underpaid when they shouldn't be.)

### Bucket target percentages

Each of P2/P3/P4 has a target percentage of its own total need, read from the `config` table (not hardcoded — same pattern as Model 1's weights, per CLAUDE.md rule 7), seeded with these defaults:

| Bucket | Target % of its own need |
|---|---|
| P2 | 75% |
| P3 | 50% |
| P4 | 40% |

### Processing order (still priority-ordered — this part didn't change)

With whatever funds remain after P0/P1 are fully set aside (via Model 2's `required_amount()`):

1. Pay P2 up to **75% of P2's total need**.
2. Then pay P3 up to **50% of P3's total need**, from whatever's left after step 1.
3. Then pay P4 up to **40% of P4's total need**, from whatever's left after step 2.
4. **If funds remain after all three targets are met**, use the leftover to top buckets back up toward 100%, in the same priority order: P2 first (toward its full 100%), then P3, then P4.

### When a bucket's own target can't be fully covered

This is the critical edge case — these percentages are **targets that get honored if funds allow, not a guarantee that manufactures money that isn't there.** If funds run out partway through a bucket's target (e.g., ₹1L is left when P4's 40%-of-need target would require ₹10L), that bucket falls back to the same within-bucket rule as before: **order that bucket's vendors by Model 1's score, highest first — fully fund each in that order until the money actually available for that bucket runs out, partially fund the one vendor where it runs out, and every vendor after that in the bucket gets zero this cycle.** The percentage target simply becomes the cap that bucket is working toward, not a floor that's paid regardless of what's actually left. Whichever bucket's target run out first, every bucket after it in priority order gets zero (same as the old rule) — the percentages don't eliminate the possibility of a bucket getting nothing, they just make it less likely by not letting P2 alone consume everything before P3/P4 get a turn.

## Guardrails

- Reuses Model 1's score directly — do not implement a separate/different scoring formula for bucket assignment. See `03-model1-weighted-scoring.md`.
- Must respect the ledger integrity rule (`07-data-pipeline-and-master-sheet.md`): no vendor's allocation can exceed what's actually outstanding, in any bucket.
- **`generate_plan()` must accept an optional eligible-vendor filter** (confirmed, for regeneration — see `06-weekly-planning-regeneration.md`), defaulting to "all vendors" so the initial monthly plan and existing tests are unaffected. An excluded vendor must not be part of the bucket/shortfall math at all when a filter is passed — not computed and then discarded, since that would still let them consume part of the funds pool internally before being thrown away.
- This model's bucket definitions (the Must Pay / Commitment category) are also referenced by `06-weekly-planning-regeneration.md`'s "Assigned Week" concept — these are two different tags on the same vendor (category/bucket = funding priority, Assigned Week = which week they're scheduled in). Don't conflate them.
- Also don't conflate this category with the code-computed "within-week execution order" described in `07-data-pipeline-and-master-sheet.md` — that's a third, unrelated concept, even though it happens to look similar (`P<n>`) in the raw Excel.
