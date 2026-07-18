# Architecture

## Two-layer design (applies to all 3 models)

Every model is built on two separate layers, so that every number Finance sees is always accurate, while still giving Finance a plain-language explanation behind each suggestion.

**Deterministic layer (does the math).** All actual calculations — aging, outstanding amounts, scores, fund splits, payment amounts — are done by fixed, rule-based Python code that reads the real vendor data directly. This is the only layer that touches the source financial data. It is predictable and auditable: the same input always produces the same output, and every number in a suggested plan can be traced straight back to the ledger.

**AI layer (explains the reasoning).** Sits on top of the deterministic layer. It never touches or recalculates the real financial data itself — it only reads the deterministic layer's output and calls its pre-built calculation functions when a number is needed. Its job is to explain, in plain language, why a plan looks the way it does (for example: "Vendor X was prioritized because its bill is over 120 days old and makes up 8% of this month's shortfall"), and to let Finance explore what-if scenarios conversationally. AI is deliberately kept out of raw arithmetic on source data, since language models are unreliable at precise calculations — every figure shown to Finance must come from the deterministic layer, never from the AI's own math.

**Implication for code structure**: keep these as physically separate modules (e.g. `deterministic/` and `ai_layer/`). The AI layer's module should have no import path to the raw data loader — it should only be able to call functions in the deterministic layer and receive their outputs.

## Step-by-step system flow

1. Month starts: the master Excel (vendor data, including each vendor's Assigned Week tag) is loaded into the system.
2. Finance enters the expected funds figure for the month — but only after Model 2 has shown them the minimum funds required (see `04-model2-min-funds-advisory.md`). The system never asks for a funds figure with no context.
3. The system generates the full month's plan across the 3 models, split into weekly sub-plans (W1-W5) based on each vendor's Assigned Week.
4. Finance reviews the suggested plan, picks whichever model fits the situation (or blends them), and makes payments day by day; each payment is logged into the system manually (no file upload/reconciliation, no ERP integration, for this build — see `07-data-pipeline-and-master-sheet.md`).
5. If Finance suspects the expected-funds figure is now wrong, they can regenerate the plan from the current week forward — see `06-weekly-planning-regeneration.md` for the exact inclusion/exclusion rules. This is the trickiest logic in the system; do not improvise it.
6. At month end, the Excel carries forward with the month's actual payment history, and the cycle repeats for the next month using the same trend and scoring logic, updated with the new month's data.

## Why this stack

- **Python** for the backend/model logic: matches the existing validated MVP model code (aging/scoring/bucket logic was already built and confirmed to match these rules), and is a reasonable fit for Excel-heavy, calculation-heavy logic.
- **React** for the frontend: agreed separately, can be built in parallel against a mocked data contract while backend/pipeline details are finalized. Keep all data-fetching behind a single API service layer so swapping mock data for the real backend doesn't require touching components.
- No database is decided yet. Don't introduce one without checking — the Excel-as-input model may or may not need a persistent store behind it depending on how `07-data-pipeline-and-master-sheet.md`'s open items resolve.
