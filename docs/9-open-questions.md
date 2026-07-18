# Open Questions

Items below are genuinely unresolved. **Do not guess or silently pick an option** — if a task depends on one of these, stop and ask the product owner (Sarath) rather than assuming.

## Still open

Nothing currently open — the real master Excel (`Vendor's Details.xlsx`) has been received and its structure confirmed. If the production sheet's layout ever differs from this file once it goes live, treat that as a new open question, not an assumption to carry over silently.

## Resolved (kept here for traceability)

- ~~Database/persistence?~~ → Resolved: SQLite, accessed through an ORM, for now — revisit if multi-user concurrency or a cloud deployment becomes real. See `12-database.md`.
- ~~Master Excel structure~~ → Resolved: real file received (`Vendor's Details.xlsx`, project root, 83 vendors × 14 months). Monthly-total structure confirmed (not invoice-level). New columns confirmed present: Assigned Week + within-week order (packed into one column, split at ingestion), and a one-off weekly (W1-W5) breakdown for the latest cycle only. Reconsider and P0/P1 tag are not in the sheet yet — both are new UI/DB additions, with Reconsider intentionally never written to Excel. See `07-data-pipeline-and-master-sheet.md` for full detail.
