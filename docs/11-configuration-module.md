# Configuration Module

## What Finance can change here

Two different categories of things are editable through this module — keep them conceptually and technically separate:

1. **Vendor data and tags** (lives in the master Excel): vendor category (`Must Pay` / `Commitment` fixed, plus `Normal` / `Inactive` / any custom category Finance has added through Configuration — see `14-new-model-2.md`; category-configuration task, 2026-08-05, made the non-fixed set extensible rather than a closed 4-value list), priority tag for cuttable vendors (P2/P3/P4/P5/custom, Finance-assigned directly), commitment months (Commitment vendors only — see `14-new-model-2.md`), Assigned Week, individual vendor field corrections (opening balance, payable, payment values, etc.). Editable two ways:
   - Directly in the UI, field by field.
   - By uploading a replacement Excel with the same column structure.
2. **System configuration** (does not live in the Excel, has no natural "row"): New Model 2's priority-bucket ceiling/floor percentages, category label, and cut order (one row per bucket — P2/P3/P4/P5 today, plus any custom category Finance adds — see `14-new-model-2.md`). Editable only through the UI — there's no Excel equivalent for these. Must Pay/Commitment appear here as ordinary rows too (2026-08-07 edit-lock-removal task): their `bucket_key`/category label/cut order are Finance-editable and reorderable exactly like any other row. The one thing that never changes is which row IS the guaranteed-funding tier — that's tracked by a separate, non-editable `pinned_role` column (`"must_pay"`/`"commitment"`/`NULL`), not by the row's display text — so renaming "P0" to something else doesn't un-guarantee its vendors. `remove_bucket()` refuses to delete a pinned row outright, regardless of whether it's in use; rename and reorder stay unrestricted.

## Write-back rule — non-negotiable

**Any edit to vendor data or tags, made directly in the UI, must write back into the actual Excel file.** The Excel is the single source of truth; the UI is a view onto it plus an editor for it, never a separate store that can drift out of sync with it. Finance's edit is the final call, and the Excel needs to reflect that immediately — don't hold edits only in application state/memory/a database row while the Excel stays stale.

This does not apply to system configuration (weights, thresholds) — those have no Excel representation, so they get their own persisted store.

**Implemented**: the `PriorityBucket` DB table (`backend/db/models.py`, see `12-database.md`) holds one row per bucket — `bucket_key`, `display_label`, `ceiling_pct`, `floor_pct`, `rotation_position`, `category_name`, and `pinned_role` (nullable — `"must_pay"`/`"commitment"` for the two guaranteed-funding rows, `NULL` for every ordinary bucket). Full CRUD lives in `backend/configuration/priority_bucket_edits.py` (`list_buckets()`/`add_bucket()`/`update_bucket()`/`remove_bucket()`), wired to `GET/POST/PUT/DELETE /config/priority-buckets` (`backend/api/routers/configuration.py`) and the Configuration tab's priority-bucket table (`frontend/src/components/ConfigurationTab.tsx`). Every add/edit/remove writes its own audit-log row (see below) and — per CLAUDE.md rule 7 — the allocator (`backend/models/new_model_2/allocator.py`) reads bucket ceilings/floors/order/pinned-role from this table on every plan generation, never from a hardcoded literal. Removing a bucket still in use by a vendor's `priority_tag` is rejected, naming the blocking vendor; removing a `pinned_role` row is rejected outright, in-use or not (`list_buckets()`'s `deletable` field tells the frontend which rows that applies to, so it can grey out Remove without hardcoding "P0"/"P1").

## Audit trail — non-negotiable, separate from the Excel itself

Every override — whether made via direct UI edit or via a replacement Excel upload — must be recorded in a separate, append-only audit log. Do not rely on Excel's own edit history or version info; this needs to be a system-maintained log outside the file itself. At minimum, each entry should capture:

- Timestamp
- What changed: vendor + field (or system setting name)
- Old value → new value
- Source of the change: UI edit vs. Excel re-upload
- Who made the change, if that's knowable (see open item below)

This log is what makes "Finance had the final call" auditable after the fact — leadership or Finance itself should be able to answer "why does this vendor's category say Must Pay now?" without guessing.

## Audit Log viewer — implemented

`AuditLog` (`backend/db/models.py`) is append-only and gets a row from every vendor-field edit, sibling-derived edit, replacement Excel upload, AI column-mapping decision, priority-bucket edit, and override/plan-run action across the codebase — see `backend/shared/enums.py::ChangeSource` for the full set of `source` values. `GET /audit-log` (`backend/api/routers/audit_log.py`) reads it back, most-recent-first, and is the only endpoint that touches this table (no PATCH/DELETE — the log itself is never editable).

Real params (Finance-friendly rework, this task): `search` (matches vendor name or ERP code), `source`, `vendor_id`, `date_from`/`date_to`, and `limit`/`offset` for pagination (default page size 100, capped at 500) — the endpoint now returns `{items, total}` rather than an unbounded flat list. It also left-joins `vendors` to return each row's `vendor_name`/`erp_code` directly, so the UI never has to show a bare numeric ID.

The viewer is the lower half of the Configuration tab (`ConfigurationTab.tsx`), below the priority-bucket table — a deliberate placement choice: Configuration is already the "system administration" surface, and a dedicated 5th nav tab would be a bigger information-architecture change than this module's own task should make unilaterally. As originally built it rendered the raw row (`field_name` as a literal code string, raw enum `source` text, bare `vendor_id`) — usable for debugging, not for a Finance reader trying to answer "why does this vendor's category say Must Pay now?" This task reworked it to be Finance-facing: vendor name + ERP code instead of a bare ID; `field_name`/`source` translated through plain-language lookups (`frontend/src/constants/enums.ts`'s `auditFieldLabel()`/`auditSourceLabel()`, next to `CATEGORY_LABEL`/`ALLOCATION_STATUS_LABEL`); money/enum-valued fields formatted the same way the rest of the app formats them; a one-line plain-English summary per row; a vendor/ERP-code search box, source-type filter, and date range; and a "Load more" pager over the new `limit`/`offset` params. A CSV-export button was added for the currently-filtered/loaded rows — useful for leadership/compliance reporting outside the app.

**Audit log revamp (2026-08-06):** an Excel upload used to write one `AuditLog` row per vendor touched (400+ rows for a full-sheet re-upload) — collapsed into a single summary row per upload (`backend/ingestion/upload.py`: "N vendors processed — X new, Y changed, Z removed"). Five previously-unlogged/generically-logged action types now get their own dedicated `ChangeSource` value and audit row: **Generate Plan** (vendor count + total allocated), **Reset Cycle** (was logged under the generic `ui_edit`, now its own `reset_cycle` source), **Export** (one row per download, naming which export — Min Funds Verification, Finalized Plan, or Vendor Analytics), **Funds Input** (the Available Funds figure Finance typed in, logged alongside Generate Plan since this codebase has no separate "submit funds" endpoint), and **Min Funds Calc** (Card 2's explicit "Calculate Minimum Funds Required" click only — never the Planning table's own much-more-frequent per-vendor Min Funds column, which stays unlogged on purpose). The Configuration tab's existing source filter dropdown gained these five as filterable options alongside the pre-existing ones. The table's own outer wrapper lost its `max-w-5xl` cap so it spans full width, matching Planning's own tables.

## Open item

No user/login system has been discussed yet for this project — it's been described as "Finance" using the tool, not named individual users. Until that's decided, the audit log's "who made the change" field may need to stay generic (e.g., "Finance — UI" or "Finance — Excel upload") rather than a named user. Revisit this if individual user accounts/access control ever get added to scope.

## Guardrails

- Never let a UI edit succeed silently without also writing to the Excel and the audit log — treat "edit accepted, write-back failed" as an error state to surface, not something to retry silently or ignore.
- A replacement Excel upload should also be diffed and logged the same way as individual UI edits (what changed, old → new), not just accepted wholesale with no record of what was different from the prior version.
- This module's vendor-tag edits (vendor category, priority tag, Assigned Week) interact directly with `14-new-model-2.md` and `06-weekly-planning-regeneration.md` — an edit made here should be reflected the next time either of those runs, with no separate sync step needed.
