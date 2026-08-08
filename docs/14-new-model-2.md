# New Model 2 — Priority-Bucket Ceiling Allocation

## Status

**Finalized by Finance and leadership as the one model going forward.**
This doc is now fully self-contained — it used to defer several shared
mechanics to `13-new-model-1.md` ("identical to New Model 1"), but that
doc, along with the three original models
(`03-model1-weighted-scoring.md`, `04-model2-min-funds-advisory.md`,
`05-model3-priority-bucket.md`), has been removed now that Finance has
decided. Everything those docs described that still applies is written
out below directly, not cross-referenced to a doc that no longer exists.

## Why this exists

Finance wants Normal vendors sorted into priority tiers, with higher
tiers protected longer when funds run short — this reflects a real way
Finance thinks about prioritization under a shortfall.

## Vendor categories and priority tags

Must Pay, Commitment, Normal, and **Inactive** are the four categories
Finance started with — Must Pay and Commitment are funded at their own
**P0/P1 ceiling/floor** first, ahead of every other bucket.

**P0/P1-ceiling task (2026-08-06):** Must Pay/Commitment's "always funded
in full, regardless of shortfall" behavior is no longer hardcoded into the
allocator — P0/P1 are now two more rows in the same Configuration-tab
priority-bucket table as P2-P5, with their own ceiling/floor, seeded at
**100%/100%** by default. At that default they behave exactly as before
(never shrink under any shortfall) — this only changes if Finance
explicitly lowers P0's or P1's floor. Unlike P2-P5, P0/P1 are **pinned**:
always funded first / cut last, not draggable, not removable, not
renamable — only their ceiling/floor percentages are Finance-editable. See
"Bucket ceilings" and "Allocation logic" below for the mechanics.

**Category-configuration task (2026-08-05):** Normal/Inactive are no longer
the only two "everything else" categories — Finance can now add further
custom categories (e.g. "Contractor") through the Configuration tab's
unified table. Every one of them, present or future, is always cuttable —
only Must Pay/Commitment (P0/P1, checked by category OR tag — see below)
land in the pinned tier; `allocator.py`'s `PINNED_BUCKET_KEYS`/
`_CATEGORY_DEFAULT_BUCKET` are what enforce this today (as of the
P0/P1-ceiling task — the older `_is_guaranteed()` helper this paragraph
used to name no longer exists, folded into the same bucket math every
other category uses).
`Vendor.category` itself is a plain string now (`backend/db/models.py`),
not a hardcoded enum, to make this possible without a schema change per
new category. See `docs/12-database.md`'s `priority_buckets` entry and
`docs/18-project-status-tracker.md`'s open-incident note before assuming
this is fully settled — an unresolved empty-DB issue from verifying this
exact change is still open as of 2026-08-06.

**Added 2026-07-22, signed off by Sarath:** Exit (this category's
original name) was added as its own category, not a Normal vendor with a
special tag — for a vendor Finance is winding the relationship down with.
Finance tags a vendor directly, the same way they set Must
Pay/Commitment/Normal (same screen, same mechanism).

**Renamed 2026-07-24, signed off by Sarath:** Exit is renamed **Inactive**
everywhere — category, priority tag, Excel column label, UI text. This is
a straight rename, not a behavior change to how a vendor gets tagged into
this category.

**Also changed 2026-07-24:** Inactive vendors get their own real,
independent priority tag, **P5** — no longer a mirrored display-only tag
like `EXIT` was. P5 behaves as a genuine bucket in this model's own
allocation math, with its own ceiling and floor (see "Bucket ceilings"
below), **no longer folding into whichever bucket is last in priority
order.** P5 is also now the **lowest**-priority bucket — cut **first** in
a shortfall, before P4 (see "Allocation logic" below). Inactive vendors
are still explicitly **not** part of the always-100%-funded guaranteed
tier — if anything they're now the least protected tier of all.

Inactive is otherwise treated **exactly like a Normal vendor** for every
other purpose — required-amount calculation (including the carry-forward
walk), dropping out of planning once `outstanding_balance` reaches 0.

Within Normal and Inactive, vendors additionally carry a **priority
tag**: P2, P3, P4 for Normal, P5 for Inactive — extensible to more later.
Finance sets and changes a vendor's priority tag directly — this is not
computed by any score or formula.

**Not in scope for this phase:** who is allowed to change priority tags
and bucket ceilings (RBAC / manager-only restriction) is deferred to a
later phase. This phase is model-logic only. The model's own logic must
still read bucket ceilings from a configurable source rather than
hardcode them (same no-hardcoding rule as everywhere else in this
project) — the editing screen and access control around that
configuration come later.

## Bucket ceilings

Each priority bucket has a **ceiling percentage** — the percentage of a
vendor's required amount (see below) that bucket receives whenever funds
are short, before any cutting happens. Starting values for this build:
**P0 (Must Pay) = 100%, P1 (Commitment) = 100%**, P2 = 95%, P3 = 90%,
P4 = 85%, **P5 (Inactive) = 50%** — confirmed with Finance 2026-07-24 for
P2-P5; P0/P1's 100%/100% default confirmed 2026-08-06 (P0/P1-ceiling
task) as the value that reproduces the old hard guarantee exactly.
Confirmed extensible — Finance can add further buckets beyond P5 with
their own ceiling, keeping the priority order clean
(P0 ≥ P1 ≥ P2 ≥ P3 ≥ P4 ≥ P5 ≥ ...). P0/P1 themselves are the one
exception to "extensible/reorderable" — they're pinned permanently at the
front of this order, never draggable.

A bucket's ceiling percentage applies against the **carry-forward-
adjusted required amount** (see "Minimum Funds Required" below) — i.e. a
P2 vendor's 95% means 95% of their real required amount (including any
leftover from a previously partial-paid tranche), not a simpler figure
like the raw monthly bill.

Each bucket also has its own **floor percentage** (see "Allocation
logic" step 6) — this is no longer a single value shared by every
bucket. **P0/P1's floor defaults to 100%** (same as their ceiling — see
above), P2/P3/P4 keep the original 25% floor; **P5 (Inactive) has its
own, lower floor of 10%** — confirmed with Finance 2026-07-24, since P5
is the lowest-priority bucket and cut first. Both ceiling and floor
percentages are stored per-bucket in a configurable table (Configuration
module), never hardcoded literals in the allocation code — P0/P1
included, as of the P0/P1-ceiling task; only their bucket_key/category/
position stay fixed (see `backend/configuration/priority_bucket_edits.py`).

## Planning month (as_of) — new, confirmed 2026-07-23

**New Model 2 needs to know which cycle Finance is actually planning for —
this is no longer inferred from "whatever the sheet's latest ingested
month happens to be," and never from today's real calendar date.**
Confirmed: Finance sometimes generates the July plan on June 30th; the
wall-clock date the button gets clicked on must never change which month
anything ages into.

- Finance explicitly picks a **month + year** (no day) before anything
  else in the flow — see "UI" below for exactly where this sits.
- This picked value is the **planning month**. The aging reference point
  ("current"/0-30 bucket) is **planning month minus one calendar month** —
  e.g. planning for August means July is 0-30, June is 31-60, and so on.
  The planning month itself (August) is the cycle being funded, not a
  bucket that ages against itself.
- **Asked once per cycle, not on every regeneration.** Finance picks the
  planning month at the very first Generate Plan click of the month; every
  regeneration after that, for the rest of that cycle, reuses the same
  planning month already stored on that cycle's `plan_run` — never
  re-prompted.
- This applies to New Model 2 only. It does not change Models 1/2/3 or New
  Model 1's own `as_of` behavior — see "Implementation note" below.

## Minimum Funds Required (New Model 2 only — new rule, confirmed 2026-07-23)

**This replaces the old "carry forward every leftover tranche, plus one
fresh tranche" rule for New Model 2.** The old rule could project an
unrealistically large number for a vendor with a big multi-month backlog
(every unpaid month, however many, all summed together, uncapped) — the
new rule deliberately caps this at **at most two months' worth of bills**,
so Finance is never asked to fund an entire historical backlog in one
cycle.

**Scope: Must Pay, Normal, and Inactive vendors — all three now share this
exact same calculation.** (Must Pay previously used a different, simpler
formula — "last month's own payable, never a function of outstanding
balance." That's gone for New Model 2; Must Pay's *required amount* is now
computed identically to Normal/Inactive. The only thing still specific to Must
Pay is the *funding guarantee* — it's still always paid 100% of whatever
this calculation produces, regardless of shortfall — see "Allocation
logic" below, unchanged.) **Commitment is excluded — see its own untouched
formula further down.**

The rule, applied per vendor, using the planning-month-derived "current"
(0-30) bucket from above:

1. **No older leftover at all — only the current month's own bill is
   outstanding.** Min Funds Required = that bill's full amount. (Trivial
   case — nothing to compare against.)
2. **Current bucket is zero (no new bill this cycle) but an older month
   is still outstanding.** Min Funds Required = that oldest month's amount
   only.
3. **An older month is outstanding, and its amount is ≤ 50% of the current
   bucket's amount** (inclusive — exactly 50% counts as qualifying).
   Min Funds Required = the oldest month's amount + a **second** month's
   amount, found by walking forward from the oldest month, one calendar
   month at a time, **skipping any month whose remaining balance is
   zero** (a ₹0 month was never a real bill outstanding — including it
   wouldn't actually pay anything, so it can't count as "the second
   month"). The first month found with a nonzero remaining balance is the
   second month, whatever amount it happens to have — **exactly two
   months total, always, no exceptions**:
   - The 50%-of-current check happens **once**, against the oldest
     month's amount only, before the walk starts. It is never re-checked
     against the running two-month sum, and the walk never continues
     looking for a third month even if oldest + second is still
     comfortably under 50% of current.
   - Since the current (0-30) month is guaranteed nonzero whenever this
     branch runs (a zero current bucket is rule 2, handled separately,
     before this check), the walk is guaranteed to terminate at or before
     the current month — it can never run past it. If every month between
     the oldest and current is zero, the second month picked ends up
     being current itself (the same result as if oldest and current were
     directly adjacent).
   - Worked examples (see this task's chat log for the full set, `Ex 1`
     through `Ex 5b`): oldest Jan-26 ₹20L, Feb-26 ₹15L, current Jun-26
     ₹100L, Mar–May all zero → 20L ≤ 50L, so Min Funds = Jan + Feb = ₹35L
     (Jun's own bill excluded). Oldest Jan-26 ₹15L, Feb/Mar-26 both zero,
     Apr-26 ₹8L, May-26 zero, current Jun-26 ₹100L → walk skips Feb and
     Mar (zero), lands on Apr → Min Funds = Jan + Apr = ₹23L (May and
     Jun excluded, even though the running total is nowhere near 50L).
4. **An older month is outstanding, and its amount is > 50% of the
   current bucket's amount.** Min Funds Required = that oldest month's
   amount **only** — the current month's own bill is *not* added on top,
   even though it's a real, current bill. This is intentional: a
   disproportionately large old debt shouldn't also drag in this cycle's
   own bill, or the total balloons further.
5. **"Oldest month" always means the single oldest month's own remaining
   amount** — never a sum across everything sitting in a coarse aging
   bucket like "120+" (which can include several distinct unpaid months).
   Always resolve at month granularity, walking oldest to newest.
6. **No outstanding balance at all.** Min Funds Required = 0.

**Commitment (unchanged):** opening balance ÷ `commitment_months`,
recomputed fresh off the *live*, currently-shrinking balance every time —
no aging/tranche walk at all. Finance maintains `commitment_months` as the
"months left" figure directly; the system does not auto-decrement it.

- **Calculated once, at the first plan generation of the month** (the
  same click that fixes the planning month above), **then frozen for the
  rest of the month.** Regenerating the plan later in the month does not
  recalculate or redisplay this figure.
- **Purely informational.** There is no validation or warning if the
  Available Funds Finance later enters comes in under this number — that's
  a judgment call for Finance, not something the system polices.

**Implementation note:** this is New Model 2-specific. `required_amount()`
/ `_carry_forward_normal_amount()` in `backend/shared/min_funds.py` are
genuinely shared infra today — not old-model leftovers waiting to be
deleted. Models 1/2/3 and New Model 1 are gone from the codebase, but
`backend/api/routers/vendors.py`'s vendor list/aging endpoint still calls
these functions live. They were **not** modified for this change — New
Model 2 got its **own new function** instead, added to `backend/shared/`
(not inside New Model 2's own package: `backend/shared/min_funds_v2.py`)
so a future model can reuse it too, without touching this shared behavior
or its tests at all.

**Resolved**: `backend/weekly_planning/planner.py`'s weekly-view builder
(which New Model 2's own generate-plan-and-weekly-view calls) now computes
each row's `minimum_required` via `required_amount_v2()` too, not the OLD
`required_amount()` above — the Planning table and the Weekly view now
agree on Min Funds Required for the same vendor. This was safe once Models
1/2/3/New Model 1 (`build_weekly_view()`'s only other historical callers)
were fully removed from the codebase; `planner.py` still uses
`min_funds.py`'s `_load_vendors_and_ledger()`, just not its
`required_amount()` anymore.

### Duplicate-vendor rule (ERP-code-only, revised 2026-08-06)

A real-data investigation (Finance manually checking Min Funds Required
against a vendor-name list) originally found 14 vendor NAMES in the real
sheet that each resolve to **two** separate `Vendor` rows under two
different ERP codes — e.g. "G-Task Services" is both a Commitment row
(₹22.3L) and a Must Pay row (₹2,659). Confirmed with Finance: a shared
vendor_name alone is **not** proof of duplication — these 14 are genuinely
two different billing lines and must keep calculating independently. A
same-name/all-fields-exact-match check was briefly added on top of the
ERP-code check below to handle that case, then removed outright on
Sarath's later, explicit call: **one duplicate check only, keyed on ERP
code** — `detect_duplicate_vendor_rows()` no longer exists.

The one remaining duplicate check lives at ingestion
(`backend/ingestion/load_excel.py`, `column_mapping.find_duplicate_erp_codes()`):
when the same **(entity, ERP code) pair** appears on more than one row in
the uploaded sheet (a data-entry error, not one vendor split across rows),
the **first** occurrence is kept — ingested and calculated normally — and
every later occurrence for that same (entity, ERP code) pair is **skipped
entirely**: never read, never overwrites the first row, never contributes
to any calculation. Loud, never silent: a `duplicate_erp_code_notes` entry
names the code, every row, and the winner, surfaced on the upload banner.
The Master Data grid marks the surviving row with a red "Duplicate" badge
and lists every skipped row in a table below the grid
(`duplicate_dropped_rows`).

**Entity-code-collision fix**: grouping by bare ERP code alone used to
flag 29 groups, but 28 of those were never real duplicates — two different
legal entities (e.g. ARS/FP) legitimately reusing the same bare numeric
code, disambiguated by the sheet's own "Unique" column and `Vendor`'s real
uniqueness key, `(entity, erp_code)` (`uq_vendor_entity_erp_code` in
`backend/db/models.py`). Those now ingest as two separate vendor rows and
never appear here at all. What's left is the one genuine same-entity
data-entry collision confirmed in the real sheet — ARS "INC" ("Phyllo
Inc." vs "PrimeRole, Inc.") — still first-row-wins, still loud, and this
fix doesn't resolve it automatically; Finance still needs to assign the
skipped vendor a distinct ERP code. Both exports below now also surface
this same pair (and any future one) on their own "Exceptions" tab, not
just the upload banner and Master Data grid.

Deliberately does **not** touch Commitment's own formula, `required_amount()`,
or anything about how a vendor's individual required amount is computed —
and does not affect two vendors that genuinely have different, unique ERP
codes, however similar their names.

## Available funds

Finance types in Available Funds at **every** plan generation and every
regeneration — free to change from one regeneration to the next,
reflecting real changes in what's actually been collected.

Only two scenarios exist:

1. **Absolute / sufficient funds** — enough to pay every vendor (Must
   Pay, Commitment, and every Normal vendor) their full required amount.
2. **Short funds** — not enough to fund everyone at 100%.

There is **no third "surplus" scenario.** Confirmed with Finance: even
when they collect more than what's needed to fund everyone at 100%, they
only ever enter the exact amount needed to do that, banking any true
excess elsewhere (FD) before it reaches this system.

## Allocation logic

1. **Must Pay and Commitment vendors (P0/P1) go through the exact same
   bucket ceiling/floor/cutting math below as every other bucket** —
   P0/P1-ceiling task, 2026-08-06. There is no separate hardcoded
   pre-funding step anymore. What makes them behave as "always funded in
   full" is purely their **default ceiling/floor of 100%/100%** (see
   "Bucket ceilings" above) — at that default, any attempted cut
   immediately re-clamps back to 100%, reproducing the old guarantee
   exactly. P0/P1 sit at the front of the priority order (funded first,
   cut last), same as before.
2. **Remaining Pool = Available Funds − (sum of currently
   locked/overridden vendors' amounts, across every bucket).**
3. **First check the absolute-funds scenario: if the Remaining Pool
   covers every unlocked vendor's full 100% required amount, that's what
   they get.** Bucket ceilings never apply in this case — they exist only
   to soften an actual shortfall, not to cap a vendor below 100% when
   funds don't require it.
4. **Otherwise (funds short of that), try giving every unlocked vendor
   their bucket's ceiling percentage simultaneously.** If the total cost
   fits within the Remaining Pool, every vendor gets their bucket's
   ceiling — done, no cutting needed. At the default P0/P1 ceiling of
   100%, this means P0/P1 already get their full amount in this step.
5. **If it doesn't fit, cut in fixed 5% steps, cycling through the
   buckets in priority order from lowest to highest, repeating:** P5,
   then P4, then P3, then P2, then P1, then P0, then back to P5, and so
   on — P5 (Inactive) is cut first since it's the lowest-priority bucket;
   P0/P1 are tried last. At their default 100% floor, an attempted cut on
   P0/P1 immediately re-clamps back to 100% and they drop out of the
   rotation without ever actually losing anything — only a Finance edit
   lowering their floor lets a cut actually land. Check after each single
   5% cut whether the new total fits within the Remaining Pool.
6. **A bucket drops out of the rotation once it reaches its own floor**
   — P0/P1's floor is 100% by default, P2/P3/P4's floor is 25%, P5's
   floor is 10% (see "Bucket ceilings" above) — cutting continues among
   whichever buckets remain active. Example: if P5 hits 10% first, the
   cycle continues P4 → P3 → P2 → P4 → P3 → P2 only, skipping P5 entirely
   from then on.
7. **Only once every bucket has separately reached its own floor and
   the total still doesn't fit is this treated as an exceptional,
   escalate-to-a-human situation** — not something the model keeps
   mechanically grinding past. Finance's own expectation is that this
   essentially never happens.
8. **New vendors with no history are treated the same as any other
   vendor in whichever bucket Finance tags them into** — there is no
   separate grace period logic specific to this model.

## Leftover funds top-up (post-allocation)

Runs automatically as the **last step** of every generate/regenerate,
only if the main allocation logic (steps 1–8 above) finishes with money
still unspent (`leftover > 0`) — a near-certainty given cuts move in
fixed 5% steps and will rarely land on an exact rupee fit against the
Remaining Pool.

Rather than let that leftover sit idle, top it up against Normal
vendors' debt — same real-world logic as "if you have the money to pay a
vendor down, you pay it, you don't hold it back on principle."

**Mechanism — a single forward sweep, P2 → P3 → P4 → P5, never backward:**

1. Start at the P2 bucket. Do one full pass through its vendors, in a
   fixed, deterministic order (vendor ID ascending — not true
   randomness, so re-running the same inputs always gives the same
   result). For each vendor: if leftover covers one more 5% step for
   them (5% of their own Min Funds Required) and they're not already at
   100%, pay it and deduct from leftover, then continue the pass to the
   rest of the bucket.
2. If that pass paid at least one vendor, run another full pass over the
   **same** bucket — leftover just changed, so someone (possibly the
   same vendor again, possibly someone else) may now fit. Keep looping
   this bucket until a pass pays zero vendors.
3. Once a bucket's pass pays zero, move forward to the next bucket
   (P2 → P3 → P4 → P5) **permanently** — never revisit an earlier
   bucket. Leftover only ever shrinks as it pays out, so if a bucket
   couldn't be satisfied once, it can't be satisfied later with less
   money either.
4. Repeat at P3, then P4, then P5 (Inactive) last, consistent with P5
   being the lowest-priority bucket. Stop entirely once P5's pass pays
   zero, or leftover reaches 0 first, whichever comes first.
5. A single vendor can receive more than one 5% top-up across successive
   passes through their bucket (never more than 5% in any single pass) —
   expected, not a bug, whenever they're the only one left in their
   bucket who still fits.

**Caps:**

- Never past 100% for any vendor — hard stop, same ledger-integrity rule
  as everywhere else (never pay more than what's owed).
- **Bucket ceilings (95%/90%/85%/50%) do NOT apply here** — this step is
  explicitly allowed to push a vendor past their bucket's ceiling if
  funds allow. Ceiling exists to manage contention during an actual
  shortfall; by the time this step runs, the main allocation logic has
  already resolved that contention, so there's no one else left with a
  competing claim on this specific leftover money.

**Scope (updated, P0/P1-ceiling task):** the sweep now runs P0 → P1 → P2 →
P3 → P4 → P5, covering every bucket, not just Normal/Inactive. At P0/P1's
default 100%/100% they're already at their required amount (or their own
outstanding-balance cap) before this step ever runs, so it's a no-op for
them exactly like before — this only matters once Finance has lowered
P0's/P1's floor and a cut actually landed; leftover then tops P0/P1 back
up first, ahead of every other bucket, consistent with them being funded
first everywhere else in this model.

**Nature:** fully automatic, system-generated — **not** a Finance
override. A vendor topped up this way is not locked; the next
generate/regenerate recomputes everything, including this step, fresh
from scratch. No special UI label is required for a top-up amount vs. a
regular bucket suggestion.

## Overrides and locking

- Finance can override any vendor's suggested amount to a custom figure
  at any time.
- **Once overridden, that vendor is locked** — excluded from the
  automatic bucket/ceiling calculation on every subsequent regeneration
  this month. The locked amount is subtracted from the Remaining Pool
  before the ceiling/cutting logic runs for everyone else.
- **Locking persists across any number of regenerations** until Finance
  explicitly overrides that same vendor again — at which point the new
  figure becomes the new locked amount going forward.
- An override is a real, final, audited figure — it does not feed into
  any future automatic calculation for that vendor. Every vendor in a
  bucket still gets that bucket's percentage; personal judgment lives
  entirely in the override itself, not in a computed history.

## The three-option block

**Trigger:** Finance overrides a vendor upward such that the total plan
no longer fits within Available Funds.

**Behavior:** block finalizing/saving the plan and present exactly three
options:

1. Reduce this vendor's override back down to the system's suggested
   amount.
2. Reduce another vendor's amount to free up the difference.
3. Increase Available Funds to cover the gap.

The system does not attempt to automatically resolve this by trimming
other vendors itself — Finance always makes the choice explicitly.

## Weekly view (W1–W5)

Purely a Finance-facing display/organizational layer — **no calculation
or validation is tied to it.**

- Each vendor has a **Priority** column in the master Excel sheet stating
  which week their payment defaults into.
- When a plan is generated, or a vendor's amount changes via override,
  the vendor's full suggested/overridden amount is placed into the single
  week bucket indicated by their Priority column — no splitting, no math.
- After that, Finance can freely rearrange, split, or shift amounts
  across weeks however they like, purely for their own planning view.
  None of this affects any calculation described above.

## Actual paid this month & top-up behavior

- When Finance has already actually paid some amount to a vendor earlier
  in the month, a later regeneration's new suggested/overridden total is
  never re-presented as if starting from zero.
- **Amount still to pay this cycle = max(0, new suggested/overridden
  total − actual amount already paid this month).**
- The underlying suggested percentage is free to rise or fall between
  regenerations, reflecting real changes in fund availability — there is
  no rule preventing a lower suggestion than a previous regeneration. If
  a new total ends up below what's already been paid, the amount still
  to pay is simply zero, never negative.

## Vendor detail card — Aging + Min Funds Required explanation (new, confirmed 2026-07-23)

Finance sees a "Min Funds" column in the main planning table and needs to
be able to open a specific vendor and see clearly *why* that figure is
what it is — this must be a live, real-computed card (same as the
planning table itself), never hardcoded/static text.

- **Aging table:** every month, oldest to newest, exactly as before — no
  filtering, no "Curr"/status tag of any kind on this table. It's a plain,
  neutral read of the amounts, nothing more.
- **Below it, a "Min Funds Required" section** shows which specific
  month(s) the calculation above picked, and a plain-English explanation
  of why. **The second month picked is not always the current month** —
  it's the first month after the oldest one with a nonzero balance, which
  only equals the current month when nothing in between is outstanding.
  The wording must name the actual month picked, never assume it's
  "this month's bill":
  - Adjacent case: *"Oldest bill: May-26, ₹10L. This month's bill:
    Jun-26, ₹100L. ₹10L is 50% or less of ₹100L, so both are included.
    Min Funds Required: ₹110L."*
  - Gap case (second month is not current): *"Oldest bill: Jan-26, ₹15L.
    Next outstanding bill: Apr-26, ₹8L. ₹15L is 50% or less of this
    month's ₹100L bill, so the oldest and next outstanding bill are
    included — this month's own ₹100L bill is not. Min Funds Required:
    ₹23L."*
  - Oldest-only case: *"Oldest bill: Mar-26, ₹60L. This is more than 50%
    of this month's ₹100L bill, so only the oldest is included. Min
    Funds Required: ₹60L."*
- **This explanation is a deterministic template, not AI-generated.**
  A fixed set of sentence templates, one per situation in the rule above
  (only-current / current-is-zero / oldest-and-current / oldest-only /
  no-outstanding), selected by which branch actually applied for that
  vendor, filled in with the real computed numbers. No LLM call of any
  kind — this is describing deterministic arithmetic, so it stays in the
  deterministic layer (CLAUDE.md rule 3), same as everywhere else.
- Applies to **Must Pay, Normal, and Inactive** vendors (the shared rule
  above). **Commitment** vendors get their own version of this same
  section, with their own template describing their own formula — e.g.
  *"Opening balance ₹240L ÷ 6 months remaining = ₹40L."*

## UI

Shown to Finance as a single "New Model 2" card/tab — the sole plan-
generation surface now that Finance has decided. Four cards, in this
fixed order, all feeding the same cycle:

1. **Calendar select** — month + year (no day). Mandatory. Shown once per
   cycle, at the very first Generate Plan click of the month (see
   "Planning month" above) — not re-shown on regeneration.
2. **Calculate Minimum Funds Required** — the action/display from
   "Minimum Funds Required" above. Depends on (1) already being picked;
   the figure is meaningless without knowing the planning month.
3. **Available Funds** — the existing input field, mandatory, entered at
   every generation and regeneration.
4. **Funds Left** — the existing post-generation display (unallocated
   money left over after the last Generate/Regenerate click).

Both (1) and (3) are mandatory before "Generate Plan" can be clicked.

## Relationship to other docs

- Fully supersedes and replaces the three original models
  (`03-model1-weighted-scoring.md`, `04-model2-min-funds-advisory.md`,
  `05-model3-priority-bucket.md`) and New Model 1
  (`13-new-model-1.md`) — all four have been removed from `docs/`.
- Access control (RBAC) for editing priority tags and bucket ceilings is
  explicitly out of scope for this phase — see "Vendor categories and
  priority tags" above.
