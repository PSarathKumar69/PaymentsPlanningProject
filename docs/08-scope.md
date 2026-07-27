# Scope

## In Scope

- Data pipeline — ingest the vendor master Excel and daily payment entries, and keep vendor balances current as Finance records each payment (payment knockoffs).
- New Model 2 — Priority-Bucket Ceiling Allocation (`14-new-model-2.md`), the single planning model Finance and leadership have finalized.
- Weekly plan splitting (W1-W5) and mid-month plan regeneration, based on each vendor's Assigned Week and Reconsider tags.
- A clean, convenient UI/UX for Finance to view vendor data, generate plans, and simulate "what-if" funds scenarios.
- A Visibility & Configuration module — a single dashboard to see all vendors, payables, aging, and payment history, plus settings to configure bucket ceilings and vendor tags. Edits write back to the master Excel and are recorded in a separate audit log — see `11-configuration-module.md`.
- Manual vendor tagging by Finance for P0 (must-pay), P1 (commitment), and P2/P3/P4 (Normal priority tiers) vendors, stored and reused across cycles.

## Out of Scope

- Actual payment execution or bank/payment-gateway integration — the tool only suggests plans; Finance executes payments outside the tool.
- Automatic vendor communication or approval workflows.
- Direct ERP/accounting system integration — pending confirmation from Finance on data source (see `9-open-questions.md`).

If a task seems to require something in the "out of scope" list to work, stop and flag it rather than building toward it.
