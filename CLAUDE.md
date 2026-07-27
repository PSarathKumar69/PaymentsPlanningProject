# Vendor Payment Planning & Prioritization Automation

Context file for Claude Code. Read this first on every session. Detailed specs live in `docs/` — read the relevant file before touching related code.

## What this project is

A tool that helps a Finance team decide, every payment cycle, how much to pay to which vendors — especially when available funds are less than total amounts owed. The tool generates suggested payment plans. **It never executes payments and never overrides Finance's judgment.** Finance reviews suggestions and makes the final call, every time.

Full background: `docs/01-objective-and-current-process.md`

## Stack

- Backend / model logic: **Python**
- Frontend: **React**
- Data input: Excel — the real master sheet (`data/Vendor's Details.xlsx`, project root) is already available and is what all code should be built/tested against — see `docs/07-data-pipeline-and-master-sheet.md`
- Database: **SQLite**, accessed through an ORM (not raw SQL), to keep a Postgres migration path open. See `docs/12-database.md` for the decision, reasoning, and rough table shape.

## Non-negotiable rules (apply to all code)

1. **Ledger integrity**: `Closing Balance = Opening Balance + Total Payable − Total Payment` must always reconcile exactly. Never write logic that can produce a negative balance or a payment greater than what's outstanding. Full detail in `docs/07-data-pipeline-and-master-sheet.md`.
2. **Suggestion-only**: nothing in this codebase ever calls a real payment/bank API. Every model's output is a proposed plan a human reviews.
3. **Deterministic vs AI layer — hard boundary**: all arithmetic (aging, scores, fund allocation, splits) happens in plain, deterministic Python code that reads the real vendor data directly. Any AI/LLM component only reads the deterministic layer's already-computed output and explains it in plain language — it must never receive raw vendor data directly or perform its own arithmetic on it. See `docs/02-architecture.md`.
4. **New Model 2's funds figure comes first — initial monthly plan only**: this is two distinct UI actions, not one button. (a) A "Calculate Minimum Funds Required" action runs the minimum-funds calculation and displays the number — no plan exists yet. (b) Only after that number is shown does the funds-input field become usable; Finance enters an amount, then a separate "Generate Plan" action produces the actual plan. Never build a flow that asks for funds before showing this figure. This two-step sequence applies **only** to the initial monthly plan — mid-month regeneration does not recalculate or redisplay the minimum-funds figure (see `docs/06-weekly-planning-regeneration.md`). See `docs/14-new-model-2.md`.
5. **P0 and P1 vendors (Must Pay / Commitment) are always assumed fully funded.** Fund shortages only ever affect P2/P3/P4 (Normal) and P5 (Inactive) — P5 was added 2026-07-24 as its own independent, cuttable bucket (cut first in a shortfall, its own ceiling/floor), no longer folded into P4 the way the old Exit tag was. Don't write logic that shrinks P0/P1 allocations under a funding shortfall.
6. **Configuration edits write back to Excel, and get audited.** Any vendor data/tag edit made in the UI must write back into the actual Excel file — it's the single source of truth, never a separate store that can drift from it. Every override (UI edit or replacement Excel upload) is recorded in a separate append-only audit log. See `docs/11-configuration-module.md`.
7. **No hardcoding — config/DB-driven, not literals in code.** New Model 2's bucket ceiling percentages (P2/P3/P4) must be read from the `config` table (`docs/12-database.md`), never hardcoded as literals inside allocation functions — Finance can change these through the Configuration module, and the code must pick that up without a redeploy. Status/state values (vendor category, priority tag, vendor payment status, plan status, Reconsider Yes/No, etc.) must be defined once in a single shared enum/constants module and referenced everywhere, never re-typed as ad hoc strings scattered through the codebase. Excel column mapping must go through a configurable mapping layer, not fixed column letters/positions — the real sheet's exact layout is still an open item (`docs/9-open-questions.md`). The number of weeks in a month (W1-W5) must be derived from the calendar, not hardcoded to always be 5. Frontend components fetch data through the API service layer (`docs/02-architecture.md`) — never embed sample/mock data directly in component code once real endpoints exist.

## The planning model

Finance and leadership have finalized **New Model 2 — Priority-Bucket Ceiling Allocation** as the single planning model. Earlier candidate models (Old Model 1/2/3, New Model 1) were explored and retired during design; they are no longer part of this codebase and their docs have been removed. Do not reference or resurrect them.

- **New Model 2 — Priority-Bucket Ceiling Allocation**: `docs/14-new-model-2.md` — vendor categories (Must Pay/Commitment/Normal/Inactive) and priority tags (P0-P5), bucket ceilings, minimum funds required, allocation logic, leftover funds top-up, overrides/locking, weekly view.

## Weekly planning & regeneration

The monthly plan splits into weekly sub-plans (W1-W5), and Finance can regenerate mid-month with specific include/exclude rules. This is easy to get subtly wrong — read `docs/06-weekly-planning-regeneration.md` in full before implementing any plan-generation or regeneration logic.

## Current project stage

MVP, actively being built out feature by feature. Implemented and verified so far: New Model 2's core allocation logic (bucket ceilings, cutting/rotation, leftover funds top-up), the Exit vendor category/priority tag, and the data-pipeline Excel upload flow (single-button upload/replace, one-slot backup + revert, generic passthrough fields for unmapped columns with auto-detected widgets, a live Master Data grid — now built into the React app's Main tab (`frontend/src/components/MasterDataGrid.tsx`), not just `test_ui.html`, which keeps its own copy as the secondary dashboard's reference implementation).

**Done since the last update**: the explicit Finance-picked planning month (month+year, no day, asked once per cycle) and the new capped Minimum Funds Required rule (oldest outstanding month vs. 50% of the current month's own bill, at most 2 months, never an unbounded leftover chain) are both implemented — `backend/shared/min_funds_v2.py` (`required_amount_v2()`/`min_funds_breakdown_v2()`/`calculate_minimum_funds_required_v2()`) plus `backend/api/routers/new_model_2.py`'s planning-month plumbing and its own `/models/5/minimum-funds-required` endpoint. `backend/shared/min_funds.py`'s original `required_amount()`/`_carry_forward_normal_amount()` were left untouched, as required — see below for what those are actually used for now.

**Old Model 1/2/3 and New Model 1's own routers, schemas, and model packages have been removed from the codebase** — `main.py` only registers New Model 2 + shared routers today. The vendor-scoring module that used to live under an old-model-named directory has been relocated: `backend/shared/scoring.py` (`score_vendors()`/`recompute_and_persist_scores()`/`read_weights()`, formerly `backend/models/model1_weighted_scoring/scorer.py`) is now framed as shared infra — `backend/shared/payment_logging.py` (vendor score/aging-bucket recompute on payment) and `backend/weekly_planning/planner.py` both call into it live, for New Model 2. The now-dead `MODEL_1_PLAN_RUN_LABEL`/`NEW_MODEL_1_PLAN_RUN_LABEL` constants, the dead old-model tab calls in `test_ui.html` (which now shows New Model 2 only, no tab bar), and the test-pipeline/demo-script artifacts (`testpipeline/`, `scripts/seed_demo.py` and friends, `backend/db/app.db`/`demo.db`) have all been removed too — the DB files are regenerated fresh (ingestion re-run against the real master sheet for `app.db`; `demo.db` left empty, to be reseeded later once the React UI is finished). **`backend/shared/min_funds.py`, and its use in `vendors.py`/`planner.py`, was explicitly left alone** (only stale comments reworded) — those are live, shared code paths New Model 2 itself depends on (the vendor list/aging endpoint, and New Model 2's own weekly-view builder), not old-model leftovers, despite older comments in those files framing them that way.

**Known open discrepancy, not yet resolved**: New Model 2's Planning table computes Min Funds Required via the new capped rule (`required_amount_v2`), but `planner.py`'s weekly-view builder still computes each row's `minimum_required` via the OLD `required_amount()` rule — so the same vendor can show two different Min-Funds figures across the Planning table vs. the Weekly view. Flagged for Sarath, not silently fixed.

Several architecture and data questions are still open — **do not assume answers to these**, check `docs/9-open-questions.md` and flag if a task depends on one of them. The "within-week execution order" rule in `docs/06-weekly-planning-regeneration.md` is flagged as an open item needing Sarath's confirmation (it referenced the old score/bucket concepts, which no longer exist).

**React UI (`frontend/`, Vite + React + Tailwind) is now fully wired to real endpoints**, not mock data — `frontend/src/api/*.ts` calls the real backend across Main (data pipeline/upload), Planning (the full New Model 2 flow — calendar picker, Min Funds card, Available Funds, generate/regenerate, vendor table with priority-tag filters and P5 support, vendor detail modal), and Configuration (priority-bucket CRUD) tabs. Analytics is still a placeholder, as planned. See `docs/15-react-ui-layout.md` — its "mock-data phase" section describes a now-completed, temporary state, not the current one. `test_ui.html` still exists as a secondary reference dashboard (it predates the React UI); the queued cleanup handoff above trims its dead old-model tabs but doesn't remove it outright.

## Docs index

| File                                          | Contents                                                                                                                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docs/01-objective-and-current-process.md`  | Why this exists, what Finance does manually today, success metrics                                                                                   |
| `docs/02-architecture.md`                   | Deterministic + AI layer split, step-by-step system flow, stack rationale                                                                            |
| `docs/06-weekly-planning-regeneration.md`   | W1-W5 split, Assigned Week tag, Reconsider flag, regeneration rules                                                                                  |
| `docs/07-data-pipeline-and-master-sheet.md` | Master Excel structure, ingestion, daily manual payment entry, ledger integrity formula                                                              |
| `docs/08-scope.md`                          | In scope / out of scope for this build                                                                                                               |
| `docs/9-open-questions.md`                  | Unresolved items — don't guess these, ask                                                                                                           |
| `docs/11-configuration-module.md`           | Vendor/tag edits + system config, Excel write-back rule, audit trail                                                                                 |
| `docs/12-database.md`                       | SQLite decision, reasoning, rough table shape                                                                                                        |
| `docs/14-new-model-2.md`                    | New Model 2 — the single planning model: categories/tags, bucket ceilings, minimum funds, allocation logic, leftover top-up, overrides, weekly view |
| `docs/15-react-ui-layout.md`                | React UI application shell — collapsible sidebar, 4-tab layout, tab 2's card/filter/table structure, stack + mock-data-phase decisions              |
| `docs/16-endpoint-ui-mapping.md`             | Every backend endpoint mapped to its React caller (or flagged as unmapped, with why) — `/rollover` and `/audit-log` have no UI entry point           |
| `docs/17-frontend-hardcoded-values-audit.md` | CLAUDE.md rule 7 audit of `frontend/` — what's a legitimate fixed constant vs. an actual should-come-from-backend gap                                 |

## Working conventions

- This is a fresh project — there is no prior code or data available anywhere for this repo. Do not assume an existing MVP's code can be found or ported; implement from the specs in `docs/` directly. Where a spec leaves a formula detail genuinely undecided (e.g., exact aging bucket boundaries, which direction a factor should push a score), implement a clearly documented, reasonable default and flag it in the code and to Sarath for confirmation — don't block waiting for an answer, and don't silently guess without flagging it either.
- Prefer plain, readable Python for the deterministic layer — this code needs to be auditable by non-engineers (Finance, leadership) if questioned.
- Keep the AI layer's code physically separate from the deterministic layer's code (e.g., separate module/package) so the "never touches raw data" rule is enforceable by code review, not just convention.
- **Keep code minimal — use libraries, don't hand-roll.** For a small, well-defined task, prefer a well-maintained library that already solves it (e.g., pandas for data manipulation and Excel I/O, standard library `datetime`/`decimal` for date and money math) over writing custom logic from scratch. Don't add abstraction layers, wrapper classes, or configuration flexibility for needs that aren't actually specified yet. If implementing something the docs describe as simple is turning into a lot of code, stop — check whether a library already does this, or whether the approach is over-engineered, before continuing.
