# New Model 1 — Consistency-Based Uniform Allocation

## Status

**This model replaces Model 1 (Weighted Scoring, `03-model1-weighted-scoring.md`),
Model 2's allocation logic, and Model 3 (Priority Bucket,
`05-model3-priority-bucket.md`) for all plan generation and regeneration,
going forward.** Finance no longer picks between three separate models —
there is one mathematical model, used for both the first plan generation of
the month and every regeneration after it.

Model 2's Minimum Funds Required calculation (`04-model2-min-funds-advisory.md`)
is **retained**, but its role changes — see "Minimum Funds Required" below.
It is no longer "one of three models Finance can choose"; it's a standalone,
model-independent piece of information Finance uses before any plan exists.

Signed off by Sarath — this is the agreed design; treat it as final unless
he says otherwise.

## Why this replaced the three-model approach

The three original models each scored/ranked vendors using aging,
outstanding balance, or bucket category as inputs. In practice, Finance's
real mental model for deciding who gets what when funds are short does not
weight those factors — it comes down to one thing: what percentage of
their money have we been able to give every vendor this month, applied
identically across the board, with Finance manually adjusting individual
vendors afterward based on their own judgment. None of the three original
scoring formulas actually matched that behavior, so rather than patch a
fourth variant on top, this model replaces them outright.

## Vendor categories (unchanged)

Must Pay, Commitment, and Normal remain the three categories. There is
**no separate "Exit" tag**. A vendor whose relationship is ending is
handled by Finance manually re-tagging them into Must Pay or Commitment,
which guarantees them full payoff through the existing guaranteed-funding
rule — no new category or code path is needed for this.

## Minimum Funds Required (standalone, model-independent)

This is a single number, calculated once per month, that exists purely to
tell Finance the floor amount they need to go raise/collect before they
even think about generating a plan. It has no further role after that —
it is not read or recalculated by anything described below.

- **Scope: every vendor**, not just Normal ones — Must Pay's and
  Commitment's full required amount, plus every Normal vendor's required
  amount (including the carry-forward calculation: if an older tranche was
  only partially paid in a prior cycle due to a shortfall, that leftover
  carries forward and is added to the next tranche's amount, chaining
  through as many tranches as have accumulated — see
  `04-model2-min-funds-advisory.md` for the detailed mechanics, unchanged).
- **Calculated once, at the first plan generation of the month, then
  frozen for the rest of the month.** Regenerating the plan later in the
  month does not recalculate or redisplay this figure.
- **Purely informational.** There is no validation or warning if the
  Available Funds Finance later enters comes in under this number — that's
  a judgment call for Finance, not something the system polices.

## Available Funds

Finance types in Available Funds at **every** plan generation and every
regeneration — this number is free to change from one regeneration to the
next, reflecting real changes in what's actually been collected.

Only two scenarios exist:

1. **Absolute / sufficient funds** — enough to pay every vendor (Must Pay,
   Commitment, and every Normal vendor) their full required amount.
2. **Short funds** — not enough to fund everyone at 100%.

There is **no third "surplus" scenario to design for.** Confirmed with
Finance: even when they collect more than what's needed to fund everyone
at 100%, they only ever enter the exact amount needed to do that, and put
the actual leftover into a Fixed Deposit outside this system entirely. Genuine
surplus beyond 100%-for-everyone never reaches this model.

## Allocation logic (runs identically for the first generation and every regeneration)

1. **Fund Must Pay and Commitment vendors first, always, at their full
   required amount.** This never shrinks, regardless of shortfall
   elsewhere — same guaranteed-funding rule as before.
2. **Remaining Pool = Available Funds − (Must Pay + Commitment total) −
   (sum of currently locked/overridden Normal vendors' amounts)** — see
   "Overrides and locking" below for what "locked" means.
3. **Among Normal vendors that are not currently locked**, run a uniform
   percentage search: try 100%, then step down in fixed **5% increments**
   (95%, 90%, 85%, 80%, 75%, ...) and find the **highest** percentage
   where the sum of (each unlocked vendor's required amount × that
   percentage) fits within the Remaining Pool.
4. **Suggest that same percentage identically to every currently-unlocked
   Normal vendor.** There is no per-vendor differentiation at the
   suggestion stage — aging, outstanding balance, and any other per-vendor
   factor play no role here. Any differentiation between vendors happens
   only through Finance's manual overrides, never automatically.
5. **New vendors with no payment history are treated exactly like any
   other Normal vendor** — no grace period, no special first-month
   treatment — unless Finance manually tags them Must Pay or Commitment.
6. **If even a very low percentage (e.g. 25%) doesn't fit for everyone**,
   this is an exceptional situation, not a normal suggestion path — flag
   it for escalation rather than continuing to mechanically step down
   toward zero. Finance's own expectation is that this should essentially
   never happen (funds are always assumed sufficient for Must Pay +
   Commitment + almost all Normal vendors).

## Overrides and locking

- Finance can override any vendor's suggested amount to a custom figure at
  any time.
- **Once overridden, that vendor is locked** — excluded from the automatic
  uniform-percentage calculation on every subsequent regeneration this
  month. The locked amount is subtracted from the pool before the search
  runs for everyone else (step 2 above).
- **Locking persists across any number of regenerations** until Finance
  explicitly overrides that same vendor again — at which point the new
  figure becomes the new locked amount going forward. If they never touch
  it again, the same locked amount just carries forward no matter how many
  more times the plan is regenerated that month.
- An override is a real, final, audited figure — but note: **it does not
  feed into any future automatic calculation for that vendor.** (This is a
  deliberate change from an earlier, now-superseded idea where a vendor's
  override history would inform a personalized future suggestion for that
  same vendor — that mechanism doesn't exist in this model. Every Normal
  vendor gets the same uniform percentage; personal judgment lives
  entirely in the override itself, not in a computed history.)

## The three-option block

**Trigger:** Finance overrides a vendor upward such that the total plan no
longer fits within Available Funds.

**Behavior:** block finalizing/saving the plan and present exactly three
options:

1. Reduce this vendor's override back down to the system's suggested
   amount.
2. Reduce another vendor's amount to free up the difference.
3. Increase Available Funds to cover the gap.

The system does not attempt to automatically resolve this by trimming
other vendors itself — Finance always makes the choice explicitly.

## Weekly view (W1–W5)

Purely a Finance-facing display/organizational layer — **no calculation or
validation is tied to it.**

- Each vendor has a **Priority** column in the master Excel sheet stating
  which week their payment defaults into.
- When a plan is generated, or a vendor's amount changes via override, the
  vendor's full suggested/overridden amount is placed into the single week
  bucket indicated by their Priority column — no splitting, no math.
- After that, Finance can freely rearrange, split, or shift amounts across
  weeks however they like, purely for their own planning view. None of
  this affects any calculation described above.

## Actual paid this month & top-up behavior

- When Finance has already actually paid some amount to a vendor earlier
  in the month, a later regeneration's new suggested/overridden total is
  never re-presented as if starting from zero.
- **Amount still to pay this cycle = max(0, new suggested/overridden total
  − actual amount already paid this month).**
- The underlying suggested percentage is free to rise or fall between
  regenerations, reflecting real changes in fund availability — **there is
  no rule preventing a lower suggestion than a previous regeneration.** If
  a new total ends up below what's already been paid, the amount still to
  pay is simply zero, never negative — there is no artificial floor
  protecting the percentage itself from dropping.

## What this replaces / relationship to existing docs

- `03-model1-weighted-scoring.md` (Weighted Scoring) — fully superseded.
- `04-model2-min-funds-advisory.md` — its allocation logic is superseded;
  its **Minimum Funds Required calculation (including the carry-forward
  tranche fix) is retained and reused as-is**, now scoped as a standalone,
  model-independent function used by every vendor category, not just
  Normal ones.
- `05-model3-priority-bucket.md` (Priority Bucket) — fully superseded.
- Must Pay / Commitment guaranteed-funding behavior, ledger integrity, and
  the suggestion-only rule (`CLAUDE.md`) all remain unchanged and apply to
  this model exactly as they did before.
