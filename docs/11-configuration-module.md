# Configuration Module

## What Finance can change here

Two different categories of things are editable through this module — keep them conceptually and technically separate:

1. **Vendor data and tags** (lives in the master Excel): vendor category (`Must Pay` / `Commitment` / `Normal` — see `05-model3-priority-bucket.md`), commitment months (Commitment vendors only — see `04-model2-min-funds-advisory.md`), Assigned Week, individual vendor field corrections (opening balance, payable, payment values, etc.). Editable two ways:
   - Directly in the UI, field by field.
   - By uploading a replacement Excel with the same column structure.
2. **System configuration** (does not live in the Excel, has no natural "row"): Model 1's factor weights (aging/outstanding/consistency/trend), Model 3's bucket score thresholds (P2/P3/P4 cutoffs). Editable only through the UI — there's no Excel equivalent for these.

## Write-back rule — non-negotiable

**Any edit to vendor data or tags, made directly in the UI, must write back into the actual Excel file.** The Excel is the single source of truth; the UI is a view onto it plus an editor for it, never a separate store that can drift out of sync with it. Finance's edit is the final call, and the Excel needs to reflect that immediately — don't hold edits only in application state/memory/a database row while the Excel stays stale.

This does not apply to system configuration (weights, thresholds) — those have no Excel representation, so they get their own persisted store (format TBD — see `9-open-questions.md`, item 10 on database/persistence).

## Audit trail — non-negotiable, separate from the Excel itself

Every override — whether made via direct UI edit or via a replacement Excel upload — must be recorded in a separate, append-only audit log. Do not rely on Excel's own edit history or version info; this needs to be a system-maintained log outside the file itself. At minimum, each entry should capture:

- Timestamp
- What changed: vendor + field (or system setting name)
- Old value → new value
- Source of the change: UI edit vs. Excel re-upload
- Who made the change, if that's knowable (see open item below)

This log is what makes "Finance had the final call" auditable after the fact — leadership or Finance itself should be able to answer "why does this vendor's category say Must Pay now?" without guessing.

## Open item

No user/login system has been discussed yet for this project — it's been described as "Finance" using the tool, not named individual users. Until that's decided, the audit log's "who made the change" field may need to stay generic (e.g., "Finance — UI" or "Finance — Excel upload") rather than a named user. Revisit this if individual user accounts/access control ever get added to scope.

## Guardrails

- Never let a UI edit succeed silently without also writing to the Excel and the audit log — treat "edit accepted, write-back failed" as an error state to surface, not something to retry silently or ignore.
- A replacement Excel upload should also be diffed and logged the same way as individual UI edits (what changed, old → new), not just accepted wholesale with no record of what was different from the prior version.
- This module's vendor-tag edits (vendor category, Assigned Week) interact directly with `05-model3-priority-bucket.md` and `06-weekly-planning-regeneration.md` — an edit made here should be reflected the next time either of those runs, with no separate sync step needed.
