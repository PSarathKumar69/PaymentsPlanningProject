# Data Pipeline & Master Sheet

## Input

The real master Excel has been received: **`data/Vendor's Details.xlsx`** — 83 vendors, 14 months (Apr'25–May'26). This is the actual sheet, not a synthetic stand-in — build and test the database, ingestion, model logic, and weekly/regeneration engine directly against it.

The real sheet has some inherent messiness to design around rather than paper over: month-column headers are a mix of strings (`"Apr-25"`) and Excel datetime objects (a serial date standing in for a month like `"Jun-25"`) — the column-mapping layer (see CLAUDE.md's no-hardcoding rule) needs to normalize both, not assume one type. **Confirmed real typo**: one column is literally labeled `"Nov'26"` but is actually Nov-25 — month header text is not trustworthy; months must be derived by position from the sheet's known start month (Apr-25), not parsed from the header label. The closing-balance column is literally labeled `"As on 31st May'26"` (a moving label, not a fixed name) — map it by position/role, not by matching that exact string. Occasional manual-entry mismatches exist too (e.g. one vendor's weekly split didn't get summed into that row's own Total column) — treat these as known real-world data-entry slips, not bugs to build elaborate reconciliation for; recompute totals from the underlying weekly/monthly figures rather than trusting a hand-typed summary cell.

## Known columns (from the original vendor ledger)

- ERP Code, Vendor Name
- Opening Balance
- Monthly Payable (one column per month)
- Monthly Payment (one column per month)
- Total Payable, Total Payment
- Closing Balance

## New columns confirmed present in the real sheet

- **Assigned Week + within-week order** (e.g. `"W2-P3"`): the real sheet packs these into a single column, but they are two different concepts and must be normalized into two separate DB fields at ingestion:
  - **Assigned Week** (W1-W5) — one column per vendor, not repeated per month. Set manually by Finance; persists until manually changed.
  - **Within-week execution order** — see `06-weekly-planning-regeneration.md`. This is **always code-computed** (by the scoring/bucket engine), never a manual tag — even though it shows up pre-filled in the current snapshot (Finance filled it in by hand before this tool existed). Going forward, the system computes and writes this, not Finance.
  - A value of `0` in this column means the vendor wasn't part of that cycle's plan at all (e.g. already fully paid) — not a real week/order value.
- **Weekly payment breakdown (W1…W5 + Total)**: the real sheet currently shows only **one** such block, for the current/latest cycle — it does not repeat per month yet, because Finance currently builds it by hand once at the start of each month and only keeps the latest. **Going forward, the system generates this breakdown every month, shows it in the UI, and persists every month's version in the database** (not just the latest) — so despite what the current snapshot looks like, treat W1-W5 as repeating per month in both the DB schema and the Excel write-back, matching how Payable/Payment already repeat per month.

## New column NOT in the real sheet, and intentionally not added to Excel

- **Reconsider** (Yes/No) — see `06-weekly-planning-regeneration.md`. Confirmed: this is **database-only**, shown in the UI for Finance to set at each regeneration, and is exempt from the Excel write-back rule in `11-configuration-module.md` — it's scoped to a single regeneration event, not persistent vendor data, so it never gets written into the Excel and no history of past values is kept.

## New column still to be added (not yet in the real sheet)

- **Vendor category** (`Must Pay` / `Commitment` / `Normal`, default `Normal`) — see `14-new-model-2.md`. Set manually by Finance through the UI once the Configuration module exists; persisted across cycles once set, and written back to Excel per rule 6. `Must Pay` maps to priority tag P0, `Commitment` maps to P1, `Normal` vendors are manually tagged P2/P3/P4 by Finance directly (not computed by any score). Don't confuse this with the within-week order value above — they look similar (`P1`, `P2`...) but are unrelated concepts.
- **Commitment months** — a number, only meaningful for vendors tagged `Commitment`. Set manually by Finance through the UI once the Configuration module exists; used by `14-new-model-2.md` to spread a Commitment vendor's current outstanding balance across that many months. Defaults to `1` (full outstanding due this cycle) until Finance sets it.

**Important**: the weekly (W1-W5) columns that DO go to Excel are a *view* onto the daily payment transaction log (see `12-database.md`'s `payments` table) plus the system's own computed allocation — not an independent figure Finance types in separately. Don't build a parallel path where weekly totals are entered directly and could drift from the daily log.

## Daily updates

Finance pays vendors daily and logs each payment into the system **manually** — there is no file upload, no reconciliation step, and no ERP/accounting-system integration in this build. Don't design the ingestion pipeline around batch file uploads for day-to-day updates; the day-to-day write path is a manual entry action, not a re-upload.

The master Excel itself is re-loaded/refreshed at the start of each month (see `06-weekly-planning-regeneration.md`'s rollover section), not continuously re-uploaded every day.

Separately, Finance can also correct/override vendor data or tags at any time through the Configuration module — those edits write back into the Excel and are logged in an audit trail. See `11-configuration-module.md`. This is distinct from daily payment logging above: one records a payment transaction, the other corrects the underlying data.

## Header row position (detected, never a fixed row number)

A second real Finance file (`data/Finance payments excel.xlsx`) surfaced a genuinely different sheet shape from the original sample: a single header row (row 1, data from row 2, no merged/master-block row at all), vs. the original sample's two-row header (row 2 merged block labels, row 3 real headers, data from row 4). Code that assumed a fixed `HEADER_ROW`/`DATA_START_ROW` (row 3/4) silently misread a real vendor data row as if it were headers against this second file — the original production bug this fix addresses.

The header row is now **detected by content**, never assumed at a fixed offset: `column_mapping.detect_header_row()` scores each of the first ~15 rows by how header-like it looks (near-100% text/labels + high column coverage for a real header row, vs. numeric-heavy for a data row or sparse for a master-block-label row), with a small bonus for literal `REQUIRED_HEADER_DEFAULTS` text found verbatim (strong evidence when headers weren't renamed). The same single AI call `ai_column_mapper.py` already makes for column-mapping confirmation also confirms or, on high confidence with a genuinely different answer, corrects this candidate — never a second Gemini call. If neither the deterministic pass nor the AI is confident, the upload is rejected with a clear error naming the ambiguity, rather than guessing. Never persisted (unlike `sheet_start_month`) — re-detected fresh on every upload, since real sheets' row layout can change again.

A final sanity gate (`column_mapping.validate_sheet_layout()`, called by `load()` right before any DB write) independently confirms the resolved header row's required-field cells are genuinely text and the first data row's money columns are genuinely numeric-or-blank — the last-resort backstop even if detection above somehow gets it wrong.

**Resolved** (was flagged, not yet resolved, as of the header-row fix above): this second real file has no W1-W5 weekly-breakdown block at all — only a single "Week" tag column. `build_sheet_map()`'s `week_cols` is now optional, not a required-columns failure: when absent, `week_cols=[]`/`week_total_col=None` and the W1-W5-seeded `PlanAllocation` history step in `load_excel.py` is skipped entirely (no error, nothing seeded) — consistent with this doc's own note above that the W1-W5 columns are a system-generated *output* written back to Excel, never a required Finance-provided input. A sheet like this ingests end-to-end today.

## AI-first column-header identification (every upload)

Real Finance sheets are messy — columns scattered in unpredictable order, headers renamed away from the sheet's usual layout, and look-alike headers that mean different fields (e.g. a vendor's own priority tag vs. which week they're assigned to). Column-header identification (`backend/ingestion/ai_column_mapper.py`) is now AI-first: on **every** upload, the AI layer identifies every mappable field (`erp_code`/`entity`/`vendor_name`/`opening_balance`/`total_payable`/`total_payment`/`category`/`commitment_months`/`assigned_week`/`priority_tag`), not just the ones a plain header-text match failed to find — a coincidental exact-text match is not proof a column still means what it used to. It's given both header rows (the merged/master block and the real headers, whichever row those actually resolve to — see above) plus real sample values, and decides by evidence, never by header wording alone.

A field with no existing match at all resolves the AI's way when confidence is high or medium and the column genuinely exists. A field that already has a match (a fixed default or a previously learned mapping) can now be **overridden**, but only on a high-confidence, genuinely different, real-column answer — anything weaker is logged as a warning and the existing mapping stays. Any actual override is loud: a dedicated audit-log entry naming the old header, the new header, and the AI's reason, plus a banner distinctly marked "AI OVERRODE..." so it's never mistaken for a routine auto-detection message. This never touches the deterministic parsing engine itself (CLAUDE.md rule 3) — the AI only ever proposes which header text a logical field corresponds to, never reads or computes a ledger figure.

## Ledger integrity (applies to every write)

`Closing Balance = Opening Balance + Total Payable − Total Payment` must reconcile exactly, for every vendor, at all times. This held with zero exceptions across 83 vendors and 14 months of real historical data — treat any code path that can violate it as a bug, not an edge case to tolerate.

Payment amount should never exceed `Opening Balance + Payable accrued to date − Payment already made` for a given vendor — i.e., never create a negative liability / effectively "overpay" a vendor.
