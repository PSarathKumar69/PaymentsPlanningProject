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

## Non-negotiable rules (apply to all code, all models)

1. **Ledger integrity**: `Closing Balance = Opening Balance + Total Payable − Total Payment` must always reconcile exactly. Never write logic that can produce a negative balance or a payment greater than what's outstanding. Full detail in `docs/07-data-pipeline-and-master-sheet.md`.
2. **Suggestion-only**: nothing in this codebase ever calls a real payment/bank API. Every model's output is a proposed plan a human reviews.
3. **Deterministic vs AI layer — hard boundary**: all arithmetic (aging, scores, fund allocation, splits) happens in plain, deterministic Python code that reads the real vendor data directly. Any AI/LLM component only reads the deterministic layer's already-computed output and explains it in plain language — it must never receive raw vendor data directly or perform its own arithmetic on it. See `docs/02-architecture.md`.
4. **Model 2's funds figure comes first — initial monthly plan only**: this is two distinct UI actions, not one button. (a) A "Calculate Minimum Funds Required" action runs Model 2 and displays the number — no plan exists yet. (b) Only after that number is shown does the funds-input field become usable; Finance enters an amount, then a separate "Generate Plan" action produces the actual plan. Never build a flow that asks for funds before showing this figure. This two-step sequence applies **only** to the initial monthly plan — mid-month regeneration does not recalculate or redisplay the minimum-funds figure (see `docs/06-weekly-planning-regeneration.md`). See `docs/04-model2-min-funds-advisory.md`.
5. **P0 and P1 vendors are always assumed fully funded.** Fund shortages only ever affect P2/P3/P4. Don't write logic that shrinks P0/P1 allocations under a funding shortfall.
6. **Configuration edits write back to Excel, and get audited.** Any vendor data/tag edit made in the UI must write back into the actual Excel file — it's the single source of truth, never a separate store that can drift from it. Every override (UI edit or replacement Excel upload) is recorded in a separate append-only audit log. See `docs/11-configuration-module.md`.
7. **No hardcoding — config/DB-driven, not literals in code.** Model 1's weights and Model 3's bucket thresholds must be read from the `config` table (`docs/12-database.md`), never hardcoded as literals inside scoring/allocation functions — Finance can change these through the Configuration module, and the code must pick that up without a redeploy. Status/state values (vendor payment status, plan status, Reconsider Yes/No, etc.) must be defined once in a single shared enum/constants module and referenced everywhere, never re-typed as ad hoc strings scattered through the codebase. Excel column mapping must go through a configurable mapping layer, not fixed column letters/positions — the real sheet's exact layout is still an open item (`docs/9-open-questions.md`). The number of weeks in a month (W1-W5) must be derived from the calendar, not hardcoded to always be 5. Frontend components fetch data through the API service layer (`docs/02-architecture.md`) — never embed sample/mock data directly in component code once real endpoints exist.

## The 3 models (independent, run in parallel)

Finance picks whichever model's suggestion fits the situation, or blends them. None is prescriptive.

- **Model 1 — Weighted Scoring**: `docs/03-model1-weighted-scoring.md`
- **Model 2 — Minimum Funds Advisory**: `docs/04-model2-min-funds-advisory.md`
- **Model 3 — Priority Bucket**: `docs/05-model3-priority-bucket.md`

## Weekly planning & regeneration

The monthly plan splits into weekly sub-plans (W1-W5), and Finance can regenerate mid-month with specific include/exclude rules. This is easy to get subtly wrong — read `docs/06-weekly-planning-regeneration.md` in full before implementing any plan-generation or regeneration logic.

## Current project stage

MVP — model logic (weights, buckets, weekly/regeneration rules) is finalized by product. Data pipeline and UI are not yet built. Several architecture and data questions are still open — **do not assume answers to these**, check `docs/9-open-questions.md` and flag if a task depends on one of them.

## Docs index

| File | Contents |
|---|---|
| `docs/01-objective-and-current-process.md` | Why this exists, what Finance does manually today, success metrics |
| `docs/02-architecture.md` | Deterministic + AI layer split, step-by-step system flow, stack rationale |
| `docs/03-model1-weighted-scoring.md` | Aging/outstanding/consistency/trend weighted score |
| `docs/04-model2-min-funds-advisory.md` | Oldest-bucket minimum funds calculation, sequential funds-entry flow |
| `docs/05-model3-priority-bucket.md` | P0/P1/P2-P4 buckets and shortfall allocation order |
| `docs/06-weekly-planning-regeneration.md` | W1-W5 split, Assigned Week tag, Reconsider flag, regeneration rules |
| `docs/07-data-pipeline-and-master-sheet.md` | Master Excel structure, ingestion, daily manual payment entry, ledger integrity formula |
| `docs/08-scope.md` | In scope / out of scope for this build |
| `docs/9-open-questions.md` | Unresolved items — don't guess these, ask |
| `docs/11-configuration-module.md` | Vendor/tag edits + system config, Excel write-back rule, audit trail |
| `docs/12-database.md` | SQLite decision, reasoning, rough table shape |

## Working conventions

- This is a fresh project — there is no prior code or data available anywhere for this repo. Do not assume an existing MVP's code can be found or ported; implement from the specs in `docs/` directly. Where a spec leaves a formula detail genuinely undecided (e.g., exact aging bucket boundaries, which direction a factor should push a score), implement a clearly documented, reasonable default and flag it in the code and to Sarath for confirmation — don't block waiting for an answer, and don't silently guess without flagging it either.
- Prefer plain, readable Python for the deterministic layer — this code needs to be auditable by non-engineers (Finance, leadership) if questioned.
- Keep the AI layer's code physically separate from the deterministic layer's code (e.g., separate module/package) so the "never touches raw data" rule is enforceable by code review, not just convention.
- **Keep code minimal — use libraries, don't hand-roll.** For a small, well-defined task, prefer a well-maintained library that already solves it (e.g., pandas for data manipulation and Excel I/O, standard library `datetime`/`decimal` for date and money math) over writing custom logic from scratch. Don't add abstraction layers, wrapper classes, or configuration flexibility for needs that aren't actually specified yet. If implementing something the docs describe as simple is turning into a lot of code, stop — check whether a library already does this, or whether the approach is over-engineered, before continuing.
