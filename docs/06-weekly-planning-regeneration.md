# Weekly Planning & Regeneration Logic

This is the most detail-sensitive logic in the system. Read this fully before implementing any plan-generation or regeneration code — small misreadings here (e.g., excluding the wrong vendors, or reassigning a vendor's week when it should stay locked) will produce plans that look plausible but are wrong.

## Concepts

- **Assigned Week (W1-W5)**: a tag on each vendor in the master Excel showing which week of the month they're scheduled to be paid. **Finance sets this manually — the system never computes or changes it.** This is a different concept from the P0-P4 priority buckets in `05-model3-priority-bucket.md` — don't conflate "week" with "priority."
- **How the weekly split actually works — confirmed, stated explicitly because it's easy to misread**: none of Models 1/2/3 know anything about weeks — they take the one total expected-funds figure and produce one amount per vendor across the whole vendor set, exactly as already built. **The weekly plan is that same result, grouped for display by each vendor's existing Assigned Week tag — it is not a separate week-by-week budget split.** There is no "Week 1's pool runs out, so Week 2 competes for what's left" mechanism — the model already decided every vendor's amount using the full month's funds before weeks ever enter the picture. Do not build week-sequential fund consumption; it isn't needed and would risk breaking the P0/P1 guarantee (which is computed across the whole month, not per week) and Model 3's P2→P3→P4 ordering (which also isn't week-scoped).
- **Expected funds**: one total figure Finance enters at the start of the month (informed by Model 2's minimum-required calculation — see `04-model2-min-funds-advisory.md`). The system uses this figure directly to generate the month's plan — it is not merely informational.
- **Reconsider flag (Yes/No)**: a per-vendor flag for any vendor currently `PARTIAL` — **REVISED (confirmed with Sarath): no longer scoped to "earlier week" specifically; Assigned Week never gates this, only payment status does.** A `PARTIAL` vendor needs Reconsider = Yes to be included in a given regeneration regardless of whether their week has technically passed; a `NOT_PAID` vendor never needs this flag at all — see the eligibility table below. Finance sets this manually, fresh, at the moment they trigger a regeneration — it is not a pre-set rule or default computed by the system. **Confirmed: this field is database-only and scoped to a single regeneration event — it is never written back to the Excel and no history of past values is kept** (unlike Assigned Week and the P0/P1 tag, which are persistent vendor data and do follow the Excel write-back rule in `11-configuration-module.md`).
- **Within-week execution order**: when a week has more vendors assigned to it than available funds can cover, the system computes an order in which that week's vendors get paid (e.g., using Model 1's score or Model 3's bucket). **This is always code-computed, never a manual Finance tag**, and it is a different concept from both the Assigned Week tag and the P0-P4 priority buckets — don't store or treat it as a third manual field.
- **Monthly payment status (confirmed, new)**: every vendor has an explicit status for the current month — `NOT_PAID`, `PARTIAL`, or `PAID_IN_FULL` — computed from their running "paid so far this month" total (see the new payment-logging section below), not inferred ad hoc each time it's needed. This belongs in the shared enum module (per CLAUDE.md rule 7), same pattern as `VendorCategory`.
- **"Paid in full" is measured against this cycle's plan allocation, not the vendor's total outstanding balance — confirmed.** A vendor's `payment_status` becomes `PAID_IN_FULL` once `paid_so_far_this_month` reaches what the current `plan_run`'s `plan_allocations` gave them for this cycle — not once their entire historical `opening_balance`/outstanding debt hits zero. A large vendor with a big multi-month balance can still be `PAID_IN_FULL` for a given cycle once they've received everything that cycle's plan allocated to them, even though they still owe money overall. Using the total outstanding balance instead would mean large vendors could never reach `PAID_IN_FULL` and would stay permanently eligible for reconsideration — that's the bug this corrects.

## Initial monthly plan generation

1. Finance clicks "Calculate Minimum Funds Required" (Model 2) and sees the number — see `04-model2-min-funds-advisory.md`.
2. Finance enters the expected funds figure, informed by that number.
3. Finance clicks "Generate Plan". The system runs the chosen model (1, 2, or 3) exactly as already built — across all vendors together, using the one expected-funds figure — producing one amount per vendor for the month. The weekly view then groups that same result by each vendor's existing Assigned Week tag (W1-W5) for display and scheduling; it does not recompute or re-divide anything per week.
4. Finance pays vendors day by day through the month; each payment is logged manually (see `07-data-pipeline-and-master-sheet.md`).

### Payment logging — confirmed prerequisite for regeneration

Regeneration needs to know, per vendor, how much has actually been paid so far this month — this must be a real, live number, not inferred from the suggested plan. Confirmed: build this now, as a small piece alongside regeneration:

- A function that records a single payment (vendor, amount, date) into the `payments` table (already in the schema, `docs/12-database.md`).
- That same function updates the vendor's running "paid so far this month" total and their `NOT_PAID` / `PARTIAL` / `PAID_IN_FULL` status (see the new "Monthly payment status" concept above).
- Ledger integrity applies here too — a logged payment can never push the running total above what the vendor is actually owed.

### Weekly view shows two numbers per week — confirmed design

For each week (W1-W5), show both of the following side by side, not just one:

- **Minimum required this week**: sum of Model 2's `required_amount()` (`04-model2-min-funds-advisory.md`) across every vendor assigned to that week. This is a fixed reference number — it does not depend on how much Finance actually funded, only on which vendors are tagged to that week.
- **Actual planned this week**: sum of the real `generate_plan()` allocations (from whichever model Finance ran) across those same vendors — what will actually get paid, based on whatever funds Finance entered.

The gap between these two numbers for a given week is exactly the signal Finance needs: a week where "actual planned" falls short of "minimum required" is a week where some vendors are only getting partially paid — and those are the vendors most likely to need a Reconsider = Yes top-up in a later week's regeneration (see the real `V00400` example below). Both numbers only coincide exactly when Finance funds precisely the minimum-required total for the month; otherwise they diverge, and that divergence is the point of showing both.

**The Must Pay/Commitment funding guarantee is inherited, not re-implemented here.** The weekly view never decides who gets shrunk under a shortfall — it only groups whatever `generate_plan()` already decided, by week. Since Model 2/3's `generate_plan()` already guarantees every Must Pay/Commitment vendor their full `required_amount()` before any Normal vendor absorbs a shortfall, a Must Pay/Commitment vendor's full amount flows into whichever week they're tagged for, untouched — the weekly grouping step must not re-shrink or re-prioritize anything on its own. (While no vendor is currently tagged Must Pay/Commitment in the real data, this must still be tested directly — don't rely on "it should follow by construction" without a test that actually tags a vendor and checks it.)

## Regeneration (triggered by Finance mid-month, any day, any number of times)

Finance regenerates when they suspect the expected-funds figure is now wrong (e.g., collections coming in higher or lower than planned). **Regeneration skips the "Calculate Minimum Funds Required" step entirely** — Finance goes straight to entering a new funds figure and triggering regeneration; the system does not recalculate or redisplay a minimum-funds number at this point (confirmed behavior — do not add this step in).

Regeneration re-runs the chosen model **once, across only the vendors still eligible** (not week by week — see the "how the weekly split actually works" note above), using the newly-entered funds figure. "From the current week forward" describes *which vendors are eligible for this re-run*, not a sequence of per-week computations.

**Confirmed: the eligible vendor set must be excluded *before* the model computes, not filtered out afterward.** An excluded vendor (e.g. already `PAID_IN_FULL`) must not be part of Model 2/3's internal computation at all during a regeneration — if it were included and then discarded, its required-amount/bucket math would still consume part of the funds pool internally before being thrown away, silently shrinking what's actually available to the genuinely eligible vendors. Model 2 and Model 3's `generate_plan()` must accept an optional eligible-vendor filter for this (defaulting to "all vendors" so the initial monthly plan and existing tests are unaffected) — this is a small, backward-compatible change to both, not a rewrite.

Apply these rules to determine eligibility:

**REVISED (confirmed with Sarath) — Assigned Week never gates eligibility.** The table below originally split `NOT_PAID`/`PARTIAL` behavior by whether their Assigned Week had already passed. That's superseded: Assigned Week is Finance-facing scheduling only (which week a vendor's allocation displays under) and must never decide whether a vendor is *in or out* of a regeneration. The only two things that matter now are payment status and (for `PARTIAL`) Finance's fresh Reconsider choice:

| Vendor state (as of regeneration moment) | Included in regenerated plan? | Notes |
|---|---|---|
| `PAID_IN_FULL` | **No** — excluded permanently | No further action for this vendor for the rest of the month. Unchanged from before. |
| `NOT_PAID` | **Yes** — always included, unconditionally | No Reconsider decision needed, regardless of Assigned Week — even a vendor whose week has technically passed. |
| `PARTIAL` | **Only if Reconsider = YES for *this specific* regeneration call** | Absent/No → locked as-is, whatever was paid stands final for the month, not touched again. A Yes from a previous regeneration does **not** carry forward — Finance must set it fresh every time (unchanged guardrail, see below). |

Assigned Week still matters for *display* only: a vendor included despite their own week having already passed shows as a "pulled forward" top-up under the **current** week (see below) — same display exception as before, just no longer an eligibility gate.

### The "pulled forward" case, precisely

When a partially-paid vendor has Reconsider = YES at regeneration time:

- Its **Assigned Week tag does NOT change** — if it was originally W1, it stays tagged W1 in the data. Do not reassign it to the current regeneration week.
- The regenerated plan **allocates an additional (top-up) amount to it, displayed under the current week**, on top of whatever partial payment it already received in its original week — this is the one exception to "group by Assigned Week tag" (the underlying tag stays W1 for record-keeping; only this new top-up transaction is shown under the current week, since that's when it's actually being decided and paid).
- It's included in the regeneration's model run **the same as any other still-eligible vendor** — no special jump-the-queue priority. Per the "how the weekly split actually works" note above, the model doesn't process week by week at all; it re-runs across every still-eligible vendor (this pulled-forward one plus everyone not yet paid) using the newly-entered funds figure, and produces one amount for each. There is no separate "current week's pool" it's competing against — it's simply part of the same single vendor set the model considers together.

### Example (from product's own walkthrough — use this as a test case)

Plan generated after W2, with some vendors partially paid in W2. Finance regenerates the plan starting from W3. Vendors from W1 and W2 with Reconsider = YES must be considered again — i.e. included in the W3 regeneration and given a top-up allocation — even though W1 and W2 have technically already passed.

### Real test case confirmed in the actual data

Vendor `V00400` ("To the New Pvt Ltd") in the real sheet has amounts in both the W2 (₹68L) and W4 (₹65L) columns for the same month, despite being tagged Assigned Week = W2. **Confirmed with product: this is a regeneration top-up, not something the initial plan does.** It was assigned W2, partially paid there, and received a top-up in W4 via a later regeneration with Reconsider = Yes — the Assigned Week tag itself never changed. Use this vendor as a real (not hypothetical) test case when building and testing the regeneration engine.

## Month-end rollover

At month end, the Excel carries forward with the month's actual payment history (not a fresh blank sheet). The next month's plan is generated fresh from that carried-forward data, using the same trend/scoring logic as the current month, with the new month's payables and Assigned Week tags added in.

## Guardrails

- Never let a regeneration silently change a vendor's Assigned Week tag — that field is Finance-owned data, not a system-computed value.
- Never auto-set the Reconsider flag — it must be explicitly set by Finance at the time of each regeneration; don't carry a previous cycle's Reconsider value forward as a default.
- Never write the Reconsider flag back to Excel — it lives in the database only, for the duration of one regeneration event.
- Ledger integrity (`07-data-pipeline-and-master-sheet.md`) still applies inside every regeneration — a top-up allocation plus prior partial payment must never exceed what the vendor is actually owed.
