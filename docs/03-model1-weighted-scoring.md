# Model 1 — Weighted Scoring Plan

## What it does

Scores every vendor using weighted factors, then uses that score to rank vendors and propose a payment plan.

| Factor | Weight |
|---|---|
| Aging | 50% |
| Outstanding amount | 0% |
| Payment consistency | 25% |
| Payment trend | 25% |

**Revised (confirmed with Sarath, supersedes the original 35/30/20/15 split
below)** — Outstanding amount's weight was dropped to 0%, and its 30% was
redistributed as a fresh 50/25/25 split across Aging/Consistency/Trend, not
a proportional scaling of the old weights.

**Why**: raw outstanding balance scales with a vendor's volume/consumption,
not with how urgent or at-risk the relationship actually is. A large
infrastructure vendor (e.g. AWS-scale) naturally carries a bigger
outstanding balance than a freelancer simply because they bill more — not
because they're more overdue or more likely to be harmed by a delay.
Weighting the score on the raw number meant big vendors structurally
out-ranked smaller ones with the exact same aging profile, which both
misrepresents actual payment risk and disadvantages smaller vendors who
typically have the least cash buffer and the most reason to walk away over
a late payment. Aging (how old the oldest unpaid amount is) is the more
direct urgency signal, so its weight absorbs what Outstanding gave up,
split evenly with Consistency and Trend (25% each) — a deliberate 50/50
balance between "how old" and "how has this vendor's payment behavior been"
overall.

**This does not leave genuinely critical vendors unprotected.** A vendor
whose nonpayment would have outsized consequences (e.g. a critical
infrastructure dependency) should be tagged Must Pay or Commitment
(`05-model3-priority-bucket.md`'s P0/P1) — those categories are guaranteed
their full required amount before Model 1's score is even consulted. Model
1's weighted score only ever governs ranking among Normal-category
vendors, so removing Outstanding's weight there doesn't remove any real
protection for vendors already flagged as critical by category.

**Outstanding amount's weight is set to 0%, not removed from the model
entirely** — it's still computed and still config-driven
(`model1.weight.outstanding` in the `config` table), so Finance can
re-introduce a nonzero weight later through the Configuration module
without a code change, consistent with CLAUDE.md's config-driven,
no-redeploy rule for these weights.

There is no prior MVP code available to port for this project (fresh repo, no existing code or data) — implement the scoring formula directly from the working definitions below. Get the exact math right rather than moving fast, since Model 3 reuses this same score for its P2-P4 bucket assignment.

## Working definitions (implement directly; flag defaults where noted)

- **Aging**: how old the vendor's oldest unpaid amount is (e.g., which aging bucket — 0-30 / 31-60 / 61-90 / 91-120 / 120+ — the oldest outstanding amount falls into). Older = higher urgency. Computed via the shared aging module (tranche-based, FIFO), not reimplemented here. **Confirmed: bucket boundaries are a whole-calendar-months-back offset from `as_of`, not a day count** — the `as_of` month itself is "0-30", 1 month back is "31-60", 2 months back is "61-90", 3 months back is "91-120", 4+ months back is "120+". Ledger tranches are monthly (one per calendar month), not daily, so day-based boundaries don't line up cleanly with real months (28-31 days each) — a day-count implementation left "31-60" structurally unreachable in the real data, since the exact-day gap between two consecutive month-start dates can jump straight from ~30 to ~61, hopping over the entire 31-60 window depending on which two months are involved. The bucket *labels* stay Finance-recognizable ("0-30" etc.) even though the criteria are month-counts, not day-counts. **Confirmed: age as of the master Excel's own latest month, not today's real calendar date** — a stale sheet (not yet updated for the current month) should not make every vendor look artificially overdue. Once the sheet is updated on a real monthly cadence, this becomes equivalent to using today's date anyway.
- **Outstanding amount**: the vendor's current total amount owed (closing balance), typically normalized/ranked relative to other vendors so a single very large vendor doesn't dominate the whole score by size alone.
- **Payment consistency**: how regularly this vendor has historically been paid relative to what it's billed. The exact formula and direction (does a historically inconsistent vendor score *higher* for attention, or *lower* for trust?) is not pinned down by product yet — implement a clearly documented default (recommended: inconsistency pushes the score up, since the point of this model is to surface vendors needing attention) and flag it clearly in the code and to Sarath for confirmation, rather than blocking on it.
- **Payment trend**: whether the vendor's payment behavior is improving or worsening over recent months (e.g., a rising or falling Payment ÷ Payable ratio over the trailing months). Window length (e.g., trailing 3 vs 6 months) is also undecided — pick a reasonable default, document it, flag for confirmation.

## Output

A ranked vendor list with each vendor's score, used to propose a suggested payment plan. This score is also consumed by Model 3 (see `05-model3-priority-bucket.md`) to assign P2/P3/P4 buckets — keep the score calculation as a single reusable function, not duplicated logic.

## Guardrails

- This model only ranks and suggests — it doesn't decide funding cutoffs or shortfalls. That's Model 2 and Model 3's job.
- Must respect the ledger integrity rule (`07-data-pipeline-and-master-sheet.md`): never suggest a payment larger than what's actually outstanding for a vendor.
