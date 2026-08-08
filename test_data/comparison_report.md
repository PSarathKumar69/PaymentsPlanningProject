# New Model 2 end-to-end validation — comparison report (2026-08-03)

Predictions were computed and frozen (`predictions_scenario_*.md`) via an
independent, from-scratch reimplementation of docs/14's rules
(`compute_predictions.py`, which does **not** import
`backend.shared.min_funds_v2` or `backend.models.new_model_2.allocator`)
BEFORE the real allocator/`generate-plan-and-weekly-view` endpoint was ever
called against this dataset. The real run happened afterward, via
`backend/validation/test_new_model_2_e2e_validation.py`, against a fresh
isolated SQLite DB (never `backend/db/app.db`) ingesting a copy of
`test_data/dummy_vendors_45.xlsx` (never `data/Vendor's Details.xlsx`).

## Result: 8/8 checks pass, after 2 real findings

The first real run surfaced exactly two discrepancies. Both are now
resolved (one by fixing my own prediction script, one by fixing a real
backend bug) and the full suite (`pytest backend/validation/ -v`) passes.

### Finding 1 — real bug, fixed: `leftover_topup_total` silently stripped from the API response

`backend/models/new_model_2/allocator.py::generate_plan()` has always
computed and returned `leftover_topup_total` (how much of the pre-top-up
leftover the top-up sweep actually spent). `POST
/models/5/generate-plan-and-weekly-view`'s Pydantic response model
(`NewModel2GeneratePlanResponse` in `backend/api/schemas/new_model_2.py`)
never declared that field, so FastAPI's `response_model` silently dropped
it before it ever reached a real HTTP caller — **the exact same failure
mode CLAUDE.md already documents twice for this codebase**
(`ai_column_mapping_messages` missing from `MasterDataCommitResponse`, and
`leftover_remaining` itself missing from this very same schema, both fixed
in earlier tasks). This one had simply never been caught because nothing
in the existing test suite asserts on this specific field through the real
HTTP endpoint.

**Fixed**: added `leftover_topup_total: float` to
`NewModel2GeneratePlanResponse`. One-line schema addition, no behavior
change to the allocator itself — the figure was always computed correctly,
just never delivered.

### Finding 2 — mispredicted (my error, not a bug): zero-outstanding-balance vendors are excluded from the plan entirely, not allocated Rs 0

My first-pass prediction assumed the 5 "branch F" vendors (fully paid off,
zero outstanding balance — one per category: `MP006`, `NM2P2-06`,
`NM2P3-05`, `NM2P4-06`, `NM2P5-05`) would appear in the plan's allocations
list and weekly view with `required_amount=0`, `allocated_amount=0`,
`status=zero`.

Actual behavior: they don't appear at all.
`backend/shared/min_funds.py::_load_vendors_and_ledger()` — shared by
`generate_plan()` and `build_weekly_view()` alike — filters out any vendor
with `has_outstanding_balance(vendor) == False` **before** either function
ever sees them. This is real, intentional, documented behavior (docs/06:
"a vendor with nothing left to pay must never re-enter
scoring/ranking/allocation/eligibility"; docs/14 cross-references the same
rule: "dropping out of planning once `outstanding_balance` reaches 0").

**Resolution**: this is a genuine gap in my first prediction, not a bug in
the model. Fixed `compute_predictions.py::allocate()` to filter out
zero-outstanding vendors up front (matching the real filter exactly), and
updated the test file's expectations accordingly. Re-verified: aggregate
totals (`guaranteed_total`, bucket percentages, leftover figures) were
unaffected either way, since a Rs 0 required-amount vendor contributes
nothing to any sum regardless of whether they're present as a Rs 0 row or
absent outright — only the per-vendor row list membership was wrong in my
first prediction.

## What matched exactly, first try, no adjustment needed

Every one of the following matched the frozen prediction (`abs` tolerance
Rs 1.00 for float-rounding only) on the very first real run:

- **Scenario 1 (Fully funded, Available Funds = Rs 90,00,000)**: every
  bucket at 100% (`bucket_pct` all `1.0`), every vendor's
  `allocated_amount == required_amount`, `leftover_remaining = Rs 2,90,000`,
  `leftover_topup_total = Rs 0` (nobody below 100% to top up).
- **Scenario 2 (Moderate shortfall, Available Funds = Rs 75,00,000)**:
  Must Pay/Commitment all `guaranteed` at 100%. Ceiling-first attempt
  (P2 95%/P3 90%/P4 85%/P5 50%) didn't fit; cutting proceeded in the
  documented P5→P4→P3→P2 rotation, 19 total 5%-cuts, landing at
  **P2=75%, P3=65%, P4=60%, P5=25%** (no bucket hit its floor). Leftover
  top-up (`Rs 49,500`) correctly swept P2 first (5 of 7 P2 vendors got one
  extra 5% step; `Lotus Paints`/P2 and the zero-required vendor did not),
  then P4 (`Xanadu Sports` got one extra step, using the exact remaining
  Rs 3,500), then retired — P3 and P5 never got a single top-up rupee,
  exactly as hand-predicted (every P3/P5 vendor's own next 5% step cost
  more than what was left when their bucket's turn came).
- **Scenario 3 (Severe shortfall, Available Funds = Rs 40,00,000)**:
  `escalation = true`, `escalation_shortfall = Rs 12,90,000` (Must
  Pay+Commitment alone already exceed Available Funds by this much) — Must
  Pay/Commitment still fully funded at 100% regardless, per docs/14's
  "never shrinks" rule. `exceptional_shortfall = true`; every P2/P3/P4/P5
  bucket cut all the way down to its own floor (P2/P3/P4=25%, P5=10%) and
  **still** didn't fit — `leftover_remaining = -Rs 7,98,000` (negative —
  see "the ambiguous case," below). `POST /models/5/finalize` correctly
  refused (`ok: false`) with `over_by = Rs 20,88,000` (total suggested
  Rs 60,88,000 − Available Funds Rs 40,00,000), matching the hand-predicted
  figure exactly. `GET /models/5/finalized-plan-export` correctly 409'd
  afterward (nothing was finalized).
- **Weekly view (W1–W5)**: every vendor's weekly-view row placed under
  exactly their own `Assigned Week` column value, no splitting, no
  pull-forward (planning month Sep-26 isn't the real current calendar
  month, so `planner.py`'s pull-forward correctly never triggers).
- **Category/priority-tag derivation**: all 45 vendors' `category`/
  `priority_tag` came back exactly as authored — no conflicts, no
  unrecognized-label fallbacks (Category and Priority Tag were set to
  agree on every row in this dataset, by design; conflict-resolution
  itself wasn't this exercise's focus).
- **Configured bucket ceilings/floors**: read fresh from the `PriorityBucket`
  DB table (seeded on this fresh DB's first `generate_plan()` call) — came
  back exactly docs/14's starting values (P2=95%/25%, P3=90%/25%,
  P4=85%/25%, P5=50%/10%), confirming this exercise's config wasn't
  silently different from what the doc describes.

## The severe-shortfall "Remaining Pool already negative" edge case — resolved, flagged for Sarath

This is new, real information from reading `allocator.py` directly, not
something docs/14 addresses:

**What the code actually does**: `funds_left` is clamped to `max(remaining,
0.0)` — never negative — so the P2–P5 ceiling/cutting logic always runs
against a non-negative number. But it does **not** clamp further than
that: when even every bucket at its own floor still costs more than
`funds_left` (here, `funds_left = 0` exactly, since guaranteed alone
already exceeds Available Funds), the code returns `exceptional_shortfall
= true` with `bucket_pct` sitting at **every bucket's floor percentage**
(P2/P3/P4 = 25%, P5 = 10%) — **not** 0%. P2–P5 vendors are NOT suggested
Rs 0 in this case; they're suggested their floor percentage of their
required amount, same as any other exceptional-shortfall case.

**The consequence, confirmed by this run**: total suggested payments
(guaranteed Rs 52,90,000 + floor-allocated Normal/Inactive Rs 7,98,000 =
Rs 60,88,000) **exceed Available Funds (Rs 40,00,000) by Rs 20,88,000** —
over 50% more money suggested than Finance actually has. This is not a
per-vendor ledger-integrity violation (each vendor's own allocation is
still ≤ their own outstanding balance) but it is an aggregate one: the
plan as a whole recommends spending money that doesn't exist. The
`leftover_remaining` field surfaces this as a **negative** number
(`-Rs 7,98,000`) rather than a real reserve — a leftover that's actually a
deficit, which reads confusingly if displayed at face value as "Funds
Left."

**Why this happens**: the floor-percentage cutting logic was designed
around the "normal" shortfall case docs/14 describes, where Remaining Pool
after Must Pay/Commitment is still positive but insufficient — it always
assumed there's *some* real money to divide among P2–P5, just not enough
for everyone's ceiling. It was never designed for (and docs/14 never
anticipated) Remaining Pool already being negative before P2–P5 logic even
starts — the exact scenario Sarath's own real production data hit. The
code's actual behavior in that case (still handing out floor percentages
against a already-negative pool) is a reasonable-looking but arguably
wrong default: arguably P2–P5 should all be suggested Rs 0 when there is
provably zero real money left for them, rather than their floor
percentage.

**Recommendation for Sarath's confirmation** (flagging, not deciding —
same house convention as every other undecided formula in this codebase):
when `remaining < 0` (Must Pay + Commitment alone already exceed Available
Funds), consider suggesting P2–P5 at a flat Rs 0 rather than their floor
percentage, and/or surfacing `escalation`/`exceptional_shortfall` together
as a single louder "this cycle needs more funds or a Finance decision on
who to cut" signal in the UI, since the current combination (`ok: false`
on Finalize, but a live "Funds Left" figure that's actually negative) is
easy to misread as "just a bit short" rather than "the P0/P1 tier alone
already can't be funded."

## Deliverables

- `test_data/dummy_vendors_45.xlsx` — the synthetic workbook (built by
  `test_data/build_dummy_workbook.py` from `test_data/dataset_spec.py`).
- `test_data/vendor_roster.md` — plain-English roster, one row per vendor.
- `test_data/predictions_scenario_1_fully_funded.md`,
  `..._2_moderate_shortfall.md`, `..._3_severe_shortfall.md` — frozen,
  independently-reimplemented predictions, written before the real model
  ever ran (see `test_data/compute_predictions.py` for the reimplementation
  itself).
- This file (`test_data/comparison_report.md`).
- `backend/validation/test_new_model_2_e2e_validation.py` — the
  re-runnable pytest suite (8 tests, all passing) exercising ingestion,
  category/tag derivation, configured bucket values, all 3 scenarios,
  finalize/export on the severe-shortfall scenario, and weekly-view
  placement. Re-run any time with:
  `python -m pytest backend/validation/test_new_model_2_e2e_validation.py -v`
- `backend/api/schemas/new_model_2.py` — one-line bug fix
  (`leftover_topup_total` added to `NewModel2GeneratePlanResponse`).

## Isolation — confirmed

- `data/Vendor's Details.xlsx` and `backend/db/app.db`: `git status`
  reports no changes to either path; file mtimes are unchanged from before
  this task started. Every ingestion call in this exercise ran through
  `POST /ingestion/load` against a `PAYMENTS_DB_PATH`-swapped, freshly
  reloaded `backend.*` module set (same pattern
  `backend/api/test_new_model_2_export.py::_fresh_app` already uses) and a
  `tmp_path` copy of the dummy workbook — never the real path.
  `backend/conftest.py`'s autouse guardrail (which raises immediately on
  any real commit or real-file overwrite) was active for every test in
  this run and never tripped.
