# Database

## Decision

**SQLite for now**, accessed through an ORM (SQLAlchemy) rather than raw SQL — this keeps the door open to Postgres later without a rewrite.

## Why

- Scale and concurrency don't justify Postgres yet: a handful of Finance users doing sequential daily entries against a few hundred to low-thousands of vendor rows, not a high-concurrency, high-write workload.
- Matches "keep it local for now" — a single file, no server process to install, configure, or secure. Trivial to back up (copy the file) or inspect directly.
- Migration path stays open: as long as the code goes through an ORM instead of SQLite-specific SQL, moving to Postgres later is mostly a connection-string change, not a rewrite.

**Revisit this decision if**: multiple Finance users need concurrent write access at real volume, there's a firm decision to deploy to a shared/cloud server (not just "eventually"), or a compliance/backup requirement specifically calls for a client-server database.

## Rough table shape

The real master Excel columns are now confirmed (see `9-open-questions.md` and `07-data-pipeline-and-master-sheet.md`) — this shape reflects that, though it's still a starting sketch to refine during implementation, not a final schema.

- **`vendors`** — current-state master data: vendor code, name, current opening balance, current tags (category, priority tag P0-P4, Assigned Week — all persistent and Excel-backed), the **Reconsider** flag (DB-only, overwritten fresh at each regeneration, never historized — see `06-weekly-planning-regeneration.md`), the **monthly payment status** (`NOT_PAID` / `PARTIAL` / `PAID_IN_FULL`, confirmed new — `06-weekly-planning-regeneration.md`), a running **paid-so-far-this-month** total, computed fields (current aging bucket).
- **`monthly_ledger`** — one row per vendor per month: month, payable, payment, opening balance, closing balance for that month. Normalizes the Excel's wide per-month columns into a queryable shape; this is what the tranche-based FIFO aging logic reads.
- **`payments`** — the daily transaction log Finance enters manually (see `07-data-pipeline-and-master-sheet.md`): vendor, date, amount, which week it was recorded against. This is the single source of truth for weekly totals — the master Excel's W1…W5 columns are computed from this table and written back, not stored as an independent input. **Confirmed: a small write function (log a payment → insert here → update the vendor's paid-so-far total and status) is a prerequisite for the regeneration engine, built alongside it.**
- **`plan_runs`** — a record of each plan generation or regeneration event: which model(s), the funds figure used, timestamp. Needed so a regenerated plan can be compared against what it replaced.
- **`plan_allocations`** — per `plan_run`, per vendor: assigned week, the code-computed within-week execution order (distinct from the P0-P4 bucket — see `07-data-pipeline-and-master-sheet.md`), allocated amount. This is what persists every month's W1-W5 breakdown, not just the latest one (the Excel snapshot only ever shows the latest; the database keeps all of them).
- **`audit_log`** — from the Configuration module (`11-configuration-module.md`): timestamp, vendor/field or system setting changed, old value, new value, source (UI edit vs. Excel re-upload).
- **`config`** — system-level settings: New Model 2's bucket ceiling percentages for P2/P3/P4 (`14-new-model-2.md`).

## Guardrails

- Do not write raw SQLite-specific SQL (e.g., SQLite-only functions/pragmas) in application code — go through the ORM so the Postgres migration path stays real, not theoretical.
- `audit_log` must be append-only — no updates or deletes to existing rows, ever, per `11-configuration-module.md`.
- Ledger integrity (`07-data-pipeline-and-master-sheet.md`) applies to whatever's stored here too — a `vendors` row's balance must always reconcile the same way it does in the Excel.
