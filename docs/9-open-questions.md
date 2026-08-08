# Open Questions

Items below are genuinely unresolved. **Do not guess or silently pick an option** — if a task depends on one of these, stop and ask the product owner (Sarath) rather than assuming.

## Still open

Nothing currently open on the sheet's COLUMN structure — a second real Finance
file (`data/Finance payments excel.xlsx`) turned up with a different header
ROW position (single header row 1, vs. the original sample's row 3 with two
pre-header rows) — this is now handled dynamically (see "Header row position"
below), not treated as a new open question, since detection generalizes to
either shape.

**Flagged during that same verification, genuinely open**: this second real
file has NO W1-W5 weekly-breakdown block at all — only a single "Week" tag
column (the assigned-week concept), not the per-week monetary split
`build_sheet_map()` requires (`week_cols`/`week_total_col`). This is a
structural sheet-shape question, not something to guess an answer to: is this
file missing that block because Finance genuinely stopped including it, or is
a different/incomplete export? Blocks this file from ingesting end-to-end
until answered — flagging rather than loosening the week-column requirement
silently.

## Header row position (this task, resolved dynamically — not a fixed answer)

The header row is no longer assumed to sit at a fixed row number — real
sheets vary (row 1 with no master-block row in one confirmed real file; row 3
with two pre-header rows in another). `column_mapping.detect_header_row()`
scores each of the first ~15 rows by how header-like it looks (text vs.
numeric content, column coverage), and the same AI call `ai_column_mapper.py`
already makes for column mapping also confirms/can override that guess. See
`column_mapping.py`'s module docstring and `resolve_header_row()`.

## Resolved (kept here for traceability)

- ~~Database/persistence?~~ → Resolved: SQLite, accessed through an ORM, for now — revisit if multi-user concurrency or a cloud deployment becomes real. See `12-database.md`.
- ~~Master Excel structure~~ → Resolved: real file received (`Vendor's Details.xlsx`, project root, 83 vendors × 14 months). Monthly-total structure confirmed (not invoice-level). New columns confirmed present: Assigned Week + within-week order (packed into one column, split at ingestion), and a one-off weekly (W1-W5) breakdown for the latest cycle only. Reconsider and P0/P1 tag are not in the sheet yet — both are new UI/DB additions, with Reconsider intentionally never written to Excel. See `07-data-pipeline-and-master-sheet.md` for full detail.
- ~~Header row always at a fixed position?~~ → Resolved: no — detected dynamically per upload, see above.
