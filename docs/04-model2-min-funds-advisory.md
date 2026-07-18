# Model 2 — Minimum Funds Advisory

## What it does

Calculates the minimum cash needed to clear just the oldest outstanding dues across all vendors, and shows this figure to Finance **first, before Finance enters any funds figure.** Once Finance enters how much cash is actually available (informed by this number), the model generates a plan for how to allocate it.

**This ordering is a hard requirement, not a UI preference.** Never build a flow where Finance types in an available-funds number with no prior context — Model 2's minimum-required figure must be calculated and displayed before that input field is even shown or usable.

## Required amount per vendor — this is the core definition, by category

Every vendor contributes one "required amount" to the minimum-funds total, and the rule for what that amount is depends on their category (confirmed by product):

- **Must Pay**: **confirmed simplification for now — Must Pay vendors are assumed to carry no outstanding balance.** Their required amount is always their **last month's Payable** (their known recurring obligation, e.g. rent). If a Must Pay vendor is ever found with a genuine outstanding balance, that's a case to flag and revisit, not silently handle by falling back to some other rule.
- **Commitment**: their **current outstanding balance ÷ a Finance-entered "commitment months" figure** — a new per-vendor field (`commitment_months`), set by Finance through the UI (Configuration module, not built yet), representing how many months the vendor's installment plan should be spread across. This replaces the earlier "last month's Payment" idea — it's more accurate since it's based on the vendor's actual current balance and Finance's own stated plan length, not a backward-looking figure that might be irregular. **Until Finance sets this via the UI, every Commitment vendor's `commitment_months` is unset** — default to `1` (i.e. treat the full outstanding as due this cycle) as an explicit, flagged interim default, not a silent guess.
- **Normal**: their oldest non-zero aging bucket amount — "oldest" is relative to that specific vendor's own aging profile (e.g. if their oldest debt is only 3 months old, that's *their* oldest bill, not a fixed global threshold).

The minimum funds figure shown to Finance is the sum of this "required amount" across every vendor, regardless of category. **This exact same per-vendor amount is also what `generate_plan` guarantees for Must Pay/Commitment vendors** (see the allocation-logic section below) — one shared definition, not two separate calculations that could drift apart.

## Flow — two distinct UI actions, not one button (initial monthly plan only)

1. Master Excel is loaded for the month.
2. Finance clicks **"Calculate Minimum Funds Required"**. This is its own action — it runs Model 2 and displays the number. No plan exists yet at this point, and no funds have been entered.
3. The funds-input field becomes usable. Finance enters the actual expected/available funds figure, informed by the number just shown.
4. Finance clicks **"Generate Plan"** — a separate action from step 2 — which produces the actual suggested allocation plan using the entered figure.

**This two-step sequence applies only to generating the initial monthly plan.** It does not repeat during mid-month regeneration — see the note below and `06-weekly-planning-regeneration.md`.

## Allocation logic when entered funds fall short of the minimum-required figure

Per CLAUDE.md rule 5, **Must Pay / Commitment vendors (P0/P1) are always assumed fully funded, in every model** — this isn't a Model 3-only rule. "Fully funded" here means their **required amount** as defined above (last month's Payable for Must Pay, outstanding ÷ commitment_months for Commitment) — not necessarily their entire multi-month outstanding balance. Model 2's "Generate Plan" step must first set aside that required amount for every Must Pay / Commitment vendor, then apply its own allocation logic to whatever funds remain across the rest ("Normal" vendors).

The exact order in which those remaining Normal vendors get paid isn't pinned down by product yet — implement a documented default and flag it, same pattern as Model 1's undecided details: **recommended default — order Normal vendors by oldest-bucket age (oldest first), tie-broken by oldest-bucket amount (largest first); fully fund each in that order until funds run out, partially fund the one vendor where funds run out, and every vendor after that gets zero this cycle (held).** This matches the model's own stated purpose — clearing the oldest bills first.

## Regeneration does NOT recalculate this figure

When Finance regenerates the plan mid-month, the system does **not** re-run "Calculate Minimum Funds Required" or show an updated minimum. Finance goes straight to entering a new funds figure and triggering regeneration — confirmed behavior, not an oversight. Do not add a recalculated-minimum step into the regeneration flow.

## Guardrails

- Never let "Generate Plan" (step 4) run before step 3's input exists — there is no valid "generate plan" state with a null/zero funds figure that wasn't deliberately entered.
- "Calculate Minimum Funds Required" (step 2) and "Generate Plan" (step 4) must be implemented as separate, independently-triggerable actions — not one button whose behavior silently changes based on state.
- Respect the ledger integrity rule (`07-data-pipeline-and-master-sheet.md`): allocations must never push any vendor's payment above what's actually outstanding.
- This model's output feeds the "expected funds" input mentioned in `06-weekly-planning-regeneration.md` — Finance's monthly expected-funds figure should be informed by this same minimum-required calculation, not independent of it. This applies at month start only, not at regeneration.
- **`generate_plan()` must accept an optional eligible-vendor filter** (confirmed, for regeneration — see `06-weekly-planning-regeneration.md`), defaulting to "all vendors" so the initial monthly plan and existing tests are unaffected. When a filter is passed, vendors outside it must not be part of the internal computation at all (not computed-then-discarded) — an excluded vendor must never consume part of the funds pool in the required-amount/allocation math.
