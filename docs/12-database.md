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

- **`vendors`** — current-state master data: vendor code, name, current opening balance, current tags (category AND priority tag are both plain strings, not fixed enums — Must Pay/Commitment stay permanently fixed/hardcoded since they're always-guaranteed, but everything else, currently P2-P5 with P5/Inactive added 2026-07-24 as its own cuttable bucket, is extensible via Configuration without a schema change — category-configuration task, 2026-08-05, converted `Vendor.category` from a hardcoded `Enum` column to `String` for exactly this reason — Assigned Week — all persistent and Excel-backed), the **Reconsider** flag (DB-only, overwritten fresh at each regeneration, never historized — see `06-weekly-planning-regeneration.md`), the **monthly payment status** (`NOT_PAID` / `PARTIAL` / `PAID_IN_FULL`, confirmed new — `06-weekly-planning-regeneration.md`), a running **paid-so-far-this-month** total, computed fields (current aging bucket).
- **`monthly_ledger`** — one row per vendor per month: month, payable, payment, opening balance, closing balance for that month. Normalizes the Excel's wide per-month columns into a queryable shape; this is what the tranche-based FIFO aging logic reads.
- **`payments`** — the daily transaction log Finance enters manually (see `07-data-pipeline-and-master-sheet.md`): vendor, date, amount, which week it was recorded against. This is the single source of truth for weekly totals — the master Excel's W1…W5 columns are computed from this table and written back, not stored as an independent input. **Confirmed: a small write function (log a payment → insert here → update the vendor's paid-so-far total and status) is a prerequisite for the regeneration engine, built alongside it.**
- **`plan_runs`** — a record of each plan generation or regeneration event: which model(s), the funds figure used, timestamp. Needed so a regenerated plan can be compared against what it replaced.
- **`plan_allocations`** — per `plan_run`, per vendor: assigned week, the code-computed within-week execution order (distinct from the P0-P4 bucket — see `07-data-pipeline-and-master-sheet.md`), allocated amount. This is what persists every month's W1-W5 breakdown, not just the latest one (the Excel snapshot only ever shows the latest; the database keeps all of them).
- **`audit_log`** — from the Configuration module (`11-configuration-module.md`): timestamp, vendor/field or system setting changed, old value, new value, source (UI edit vs. Excel re-upload).
- **`config`** — system-level settings: New Model 2's bucket ceiling percentages for P2/P3/P4/P5 (`14-new-model-2.md`), the AI-resolved `sheet_start_month` override.
- **`priority_buckets`** — New Model 2's own bucket table (`14-new-model-2.md`): bucket key, **category_name** (added 2026-08-05 — the Finance-facing category label this row belongs to, e.g. "Normal"/"Inactive"/a custom name Finance typed; several rows can share one, since P2/P3/P4 all say "Normal"), ceiling %, floor %, rotation position (drives the Configuration UI's drag-to-reorder cut sequence — top of the list = cut last) — Finance-editable via Configuration, auto-seeded with documented defaults the first time it's read empty (`allocator.py`'s `_seed_and_read_buckets()`), never hardcoded in allocation code (CLAUDE.md rule 7). **Open incident as of 2026-08-05/06** (`docs/18-project-status-tracker.md`): this table and `vendors` were both found empty in the real `app.db` during a verification pass — not yet resolved, see that doc before trusting either table's current live state.
- **`column_mapping_overrides`** — AI-resolved Excel header-to-field mappings (`ai_column_mapper.py`), persisted so a renamed column doesn't need re-resolving by AI on every future upload.
- **`vendor_extra_fields`** — generic passthrough storage for Excel columns the mapping layer doesn't recognize as a named field, with auto-detected display widgets (data-pipeline upload flow).

## Guardrails

- Do not write raw SQLite-specific SQL (e.g., SQLite-only functions/pragmas) in application code — go through the ORM so the Postgres migration path stays real, not theoretical.
- `audit_log` must be append-only — no updates or deletes to existing rows, ever, per `11-configuration-module.md`.
- Ledger integrity (`07-data-pipeline-and-master-sheet.md`) applies to whatever's stored here too — a `vendors` row's balance must always reconcile the same way it does in the Excel.
