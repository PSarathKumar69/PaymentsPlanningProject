# Project Status Tracker

Running checklist of what's done vs. outstanding. Update this file whenever
something in the list below gets completed or a new gap is found — don't
let it drift out of sync with reality the way `CLAUDE.md`'s "Current
project stage" section has in the past. This doc is the priority-ordered
backlog; `CLAUDE.md` stays the higher-level narrative summary.

## Completed

- New Model 2 (Priority-Bucket Ceiling Allocation) core logic — bucket
  ceilings, cutting/rotation, leftover funds top-up. `docs/14-new-model-2.md`.
- P5 / Inactive vendor category + priority tag, as its own independent,
  cuttable bucket.
- Data-pipeline Excel upload flow — single-button upload/replace, one-slot
  backup + revert, generic passthrough fields with auto-detected widgets.
- Explicit Finance-picked planning month + the new capped Minimum Funds
  Required rule (`required_amount_v2`, `min_funds_v2.py`), asked once per
  cycle.
- Old Model 1/2/3 and New Model 1 fully removed (routers, schemas, model
  packages, dead `PlanRun` labels); vendor-scoring module relocated from
  `models/model1_weighted_scoring/` to `shared/scoring.py` as shared infra.
- Test-pipeline/demo artifacts wiped (`testpipeline/`, `scripts/` demo/seed
  scripts, old `app.db`/`demo.db`) — to be regenerated fresh later, not yet
  rebuilt.
- React UI (`frontend/`) built out and wired to real endpoints — Main
  (upload + Master Data grid), Planning (full New Model 2 flow), and
  Configuration (priority-bucket CRUD) tabs. Analytics still a placeholder.
- Endpoint-to-UI mapping audit (`docs/16-endpoint-ui-mapping.md`) and
  hardcoded-values audit (`docs/17-frontend-hardcoded-values-audit.md`).
- ~~Master Data grid built into the React app (`MasterDataGrid.tsx`)~~ —
  **correction (2026-07-29)**: this was wrong. `MasterDataGrid.tsx` exists
  but is never imported/rendered by `App.tsx`/`MainTab.tsx` — confirmed
  dead code, caught during the live UI bug-fixing round below. The real,
  reachable vendor grid in the running app is `PlanningView.tsx`'s own
  table; every grid-related fix since has actually been touching that
  file, not `MasterDataGrid.tsx`. Not yet decided whether to wire
  `MasterDataGrid.tsx` in, merge it into `PlanningView.tsx`, or delete it.
  **Resolved (Main-tab revamp task, 2026-07-31)**: wired in for real — see
  the entry below.

## Fix Generate Plan 400 (negative aging tranche) + Master Grid datetime headers (this task)

Two confirmed bugs from a live-blocking investigation, both fixed:

1. **Generate Plan crashed with a 400** whenever the resolved `as_of`
   (planning month minus one) trailed the sheet's own latest ledger
   month — a real, reproducible production case (planning month `2026-06`
   -> `as_of=2026-05`, but the sheet's latest ingested row is `2026-06`).
   `bucket_for_months_back()` (`backend/shared/aging.py`) correctly
   rejects a negative months-back everywhere else, but nothing upstream
   handled a ledger row dated at/after `as_of` — `compute_vendor_aging()`
   -> `score_vendors()` -> `build_weekly_view()` let the `ValueError`
   escape uncaught, and `main.py`'s global handler turned it into a
   misleading 400 (looked like a planning-month input error, wasn't).
   Decision (flagged, house style): clamp `months_back` to `0` rather than
   exclude the row — `docs/14` frames the planning month's own cycle as
   "being funded, not aged against itself," and excluding it outright
   risked silently dropping real money from `total_outstanding`.
   `bucket_for_months_back()` itself stays untouched (still a guard
   elsewhere); `compute_vendor_aging()` now clamps via `max(0,
   _months_back(...))` in both places it computes an offset, and
   `payable_by_mb`/`payment_by_mb`/`remaining_by_months_back` all
   accumulate (`+=`) instead of overwrite, so two real months clamping
   into the same bucket sum rather than one erasing the other. One
   residual, explicitly-flagged-not-fixed limitation: 2+ ledger rows both
   dated after `as_of` would lump into one combined "oldest" figure
   instead of isolating the true oldest — judged too rare a compounding
   case to engineer around right now.
2. **Master Grid showed raw datetime strings as column headers**
   (e.g. `"2026-04-26 00:00:00"`) — the real sheet mixes plain-string and
   literal `datetime`-typed header cells in the same monthly block;
   `grid.py`'s `_describe_columns()` did a bare `str(cell.value)` with no
   date handling. Fixed with a new `format_header_value()`
   (`column_mapping.py`, next to `_parse_header_month()`) —
   `strftime("%b-%y")` for a real date/datetime, `str().strip()` otherwise
   (identical to today's behavior for every real text header) — wired
   into both `grid.py` and the same latent spot in
   `unmapped_header_columns()`.

**Independently verified (this session)**: read `aging.py`'s
`compute_vendor_aging()` — the `max(0, ...)` clamps, the accumulating
`+=` maps, and the flagged residual-limitation comment all match the
report exactly; `bucket_for_months_back()` confirmed untouched. Ran
`test_aging.py` + the new `test_column_mapping.py` myself: 22/22 pass.
Read `format_header_value()` and confirmed it's wired into both `grid.py`
and `unmapped_header_columns()`. Most importantly, reproduced the actual
real-data bug directly: replayed the exact request that previously
returned 400 (`available_funds=1,000,000`, `planning_month=2026-06`)
against a copy of the real `app.db` — now returns a real 200 with
`plan`/`weekly_view`/`total_overridden`/`funds_left_for_regeneration`,
confirming Finance is genuinely unblocked, not just that the unit tests
pass in isolation. Full-suite claim (71 failed / 329 passed, all
pre-existing/unrelated) spot-checked rather than fully re-run — this
sandbox is too resource-constrained to run the whole ~400-test suite in
one pass (repeatedly timed out even with `pytest-xdist` parallelism, well
short of what the reporting session's own machine needed). Of the 71,
personally reproduced 2 as genuinely pre-existing and unrelated:
`test_scoring.py`'s 3 failures all trip `conftest.py`'s real-DB-write
guardrail before reaching any real logic (a known, still-open isolation
gap, not caused by this task), and
`test_minimum_funds_required_missing_planning_month_is_422` asserts a 422
for a param that an earlier, already-completed task made optional —
genuinely stale. Did not re-verify the remaining ~69.

## AI Screen revamp — consolidated CompanionPanel + VendorDetailModal AI card (this task)

Merged the two previously separate, differently-styled AI-explanation UIs
(`CompanionPanel.tsx`'s floating-widget talking points and
`VendorDetailModal.tsx`'s own inline zero-status panel, which called the
separate `/ai/talking-scripts` endpoint) into one shared
`frontend/src/components/AiCompanionCard.tsx`, used from both entry
points. `POST /ai/vendor-talking-points`'s response schema
(`backend/api/schemas/ai_layer.py`) now also returns
`category`/`priority_tag`/`required_amount`/`allocated_amount`/
`aging_bucket` — data the fact-pack already computed but the API
previously discarded — so the card can show a header (name/ERP/category
chip/priority tag/allocation-status badge), a key-facts row
(Required/Allocated/% funded/aging bucket, `cut_from_full` warning chip),
and the AI narrative with Copy/Regenerate controls and a proper loading
skeleton, not just the old `script_text` blob. `postTalkingScripts` (the
TS wrapper for the now-unused-from-React `/ai/talking-scripts` endpoint)
removed as dead code from `frontend/src` — confirmed zero remaining
references. Test count: 19/19 passing across `backend/ai_layer/` (15) +
`backend/api/test_ai_layer_companion.py` (4) combined — this two-file
split is what the report's "19/19" figure actually refers to, not a
single directory (see verification note below).

**Independently verified (this session)**: read `AiCompanionCard.tsx` in
full — confirms the header chips, key-facts row, `cut_from_full` chip,
Copy/Regenerate buttons, loading skeleton, and error state all as
reported. Confirmed `backend/api/schemas/ai_layer.py` carries the 5 new
fields, confirmed `postTalkingScripts` has zero references left anywhere
in `frontend/src`, confirmed `ALLOCATION_STATUS_LABEL`/
`ALLOCATION_STATUS_BADGE_CLASS` exist in `frontend/src/constants/enums.ts`.
Resolved an apparent 15-vs-19 test-count discrepancy from my own initial
run (`pytest backend/ai_layer/ -q` alone gave 15): the report's "19/19"
scope includes `backend/api/test_ai_layer_companion.py` too (a
router-level test file living outside the `ai_layer/` package) — running
both together gives exactly 19 passed, confirmed directly. Not a real
discrepancy, just a narrower first selection on my part.

**Found one real gap, not just a smoothing-over**: Sarath's instruction
was to replace the small 320x460px floating-corner panel with "a proper
centered modal/dialog... sized generously" for the floating-button entry
path too. `AiCompanionCard` does support both a `variant="modal"` (the new
big centered card) and a `variant="popup"` (the old small corner style,
~384px wide, bottom-right-anchored) — but `CompanionPanel.tsx` (the
floating-FAB entry point) still explicitly passes `variant="popup"`, so
that path keeps the old cramped corner card, just now sharing code with
the modal variant rather than actually being replaced by it. Only
`VendorDetailModal.tsx`'s "Ask AI" link (which passes no `variant`, so it
gets the default `"modal"`) got the bigger centered card. The floating
button itself genuinely is bigger (`w-14 h-14`, confirmed), matching that
part of the ask — but the card it opens did not change shape. Flagging
this back to Sarath rather than treating the task as fully done on point
2 of the prompt.

## Intelligent Category<->Priority Tag derivation + planning-month suggestion

Real file's actual "Category" column (`Vendor`/`Non Vendor`, 450/2) matches
neither Finance vocabulary — confirmed not a mapping bug, a different
signal entirely, previously silently defaulting every vendor to `Normal`.
Its "Prioity" (typo, real header) column holds real `P0`-`P4` values. Fix:
`category_from_label()` (alias-normalized: case/space/hyphen variants) +
`derive_category_and_priority_tag()` (`column_mapping.py`) reconcile
category and priority tag as two views of one signal — forward (`P0`->Must
Pay, `P1`->Commitment, `P2`/`P3`/`P4`->Normal, `P5`->Inactive) and reverse
(string label present, no tag -> derive tag, `Normal`->`P2` confirmed
default, never P3/P4/blank). Confirmed-with-Sarath conflict rule: if both
are present and disagree, **category label wins**, logged loudly (never
silent) via a new `category_priority_conflict` audit-log entry +
`data_quality_notes`/upload banner; a whole-column mismatch (this real
file's actual case) aggregates into one note instead of flooding per-vendor.
`vendor_edits.py` deliberately left untouched — auto-linking category/tag
on a single-field UI edit is its own UX call, flagged for Sarath, not
decided here.

Also added: `GET /models/5/suggested-planning-month` (sheet's real last
recorded ledger month + 1, global max across vendors) and a non-blocking
`planning_month_warning` when Finance's pick disagrees — the same rule
Sarath stated ("data till April -> plan for May").

**Independently verified (this session)**: read `column_mapping.py` in
full — `_CATEGORY_LABEL_ALIASES`/`PRIORITY_TAG_TO_CATEGORY`/
`CATEGORY_TO_DEFAULT_PRIORITY_TAG`/`category_from_label()`/
`derive_category_and_priority_tag()` all match the report's described
logic exactly, including the confirmed `Normal`->`P2` default and
category-wins conflict resolution. Confirmed wiring into `load_excel.py`
(`category_priority_conflicts` list, `AuditLog` field
`category_priority_conflict`) and `upload.py`'s banner. Ran
`test_category_priority_mapping.py` myself: 30/30 pass. Confirmed
`vendor_edits.py`'s large git diff is unrelated to this task — it's the
concurrent Main-tab-revamp session's own header-row/`sheet_start_month`
plumbing change (grepped for every symbol this task introduced: zero
matches in that diff). Re-derived the real file's category/tag counts
myself with the "Prioity" header override applied explicitly (the real
upload path already has this persisted via the AI mapper; a bare fresh-DB
call without it defaults everyone to Normal, which is what a first,
naive verification attempt on my end showed before I corrected the
methodology): `Must Pay 148 / Commitment 245 / Normal 29 (23+5+1)` —
matches the report's claimed distribution exactly. Confirmed idempotent
on re-ingestion (0 drift, 0 conflicts on the real file, since its own
literal Category column never resolves against either vocabulary).
Could not run the full `backend/ingestion/` suite to completion in this
sandbox (known timeout constraint on this directory, ~88 tests, some
touching AI-mapper code) — a partial run showed 44 consecutive passes
before hitting the same known pre-existing failures pattern; nothing
checked contradicts the reported 78 passed/10 failed, identical
pre-existing failure set.

## Fix vendors.py percentage + duplicate-ERP-code warning + test-isolation audit (this task chain)

Three linked pieces, all confirmed and fixed:

1. **`vendors.py`'s payment-tracking percentage was wrong** —
   `backend/api/routers/vendors.py:159` called the old, retired-model-era
   `required_amount()` (`backend/shared/min_funds.py`) instead of New
   Model 2's `required_amount_v2()`, giving Must Pay vendors "last month's
   raw payable" instead of the real min-funds figure — confirmed on real
   vendor V00400 (98.13% real vs. 89.96% shown). Swapped to
   `required_amount_v2()`, same fix `planner.py` already had for this
   exact bug class.
2. **Duplicate ERP codes silently dropped real vendors** — confirmed 29
   ERP codes appear twice in the real sheet, unrelated vendors sharing a
   code by data-entry error (not one vendor split across rows), so a
   later row silently overwrote an earlier one with zero trace — 33
   vendors missing across Must Pay/Commitment/Normal counts. Confirmed
   with Sarath: warn loudly, still proceed (last-row-wins unchanged,
   never block the upload) — new `duplicate_erp_code_notes` surfaced on
   the upload banner.
3. **Real production data got corrupted by a test, a third time this
   project** — `test_vendor_edits.py::test_category_updates_db_excel_and_audit_log`
   ran for real against `backend/db/app.db` (`session=None`), asserted a
   stale hardcoded "V00400 starts Normal" assumption, and its `finally`
   block force-reverted the vendor to that same stale value regardless of
   whether the assertion passed — corrupting the real vendor to
   `NORMAL`/`P0` (an inconsistent combination) and deleting the audit-log
   row for it in the same revert. Root-caused precisely via the audit
   log's own absence of a `category` entry despite the DB showing the
   changed value. Fixed at the structural level, not just this one test:
   rewrote `test_vendor_edits.py` onto the same `_fresh_db_and_master(tmp_path)`
   isolation every other test file already uses (deleting the "run for
   real, then revert" pattern entirely), audited all 19
   `SessionLocal`/`load()`/`commit_upload()`/`update_vendor_field()`-touching
   test files (only this one had the gap — everything else was already
   fixture-isolated, tmp_path-isolated, or rollback-only-by-design), and
   added `backend/conftest.py` — an autouse fixture for every test under
   `backend/` that raises `RuntimeError("TEST ISOLATION VIOLATION")`
   immediately if any test ever actually commits against the real
   `app.db` or overwrites the real Excel/backup file, regardless of which
   test does it or why.

**Independently verified (this session)**: read `vendors.py` — confirmed
the `required_amount_v2()` swap. Read `load_excel.py`/`upload.py` —
confirmed `duplicate_erp_code_notes` wiring into the upload banner. Ran
both new tests directly: `test_vendor_payment_tracking_min_funds_required_uses_v2_not_old_formula`
and `test_load_duplicate_erp_code_last_row_wins_and_warns_loudly` both
pass. Read the rewritten `test_vendor_edits.py`'s own docstring — matches
the root-cause narrative exactly (own words: the revert pattern was a
structural risk, not just one bad hardcoded value). Read
`backend/conftest.py` in full — the `_forbid_real_writes` autouse fixture
correctly guards `SASession.commit`/`os.replace` against the real DB/Excel
paths specifically (not merely "session opened against the real path"),
matching the stated design that keeps the 7 legitimate rollback-only
real-DB readers working. Independently re-simulated both violations
myself (the report's own probe script wasn't left in the repo) by writing
a throwaway test inside `backend/` so the autouse fixture actually
applied: a session bound to the real `app.db` calling `.commit()` raised
`TEST ISOLATION VIOLATION` immediately; `os.replace()` targeting the real
Excel path did too (confirmed by the `pytest.raises` block succeeding,
though my own cleanup step afterward hit an unrelated sandbox file-
permission error). Confirmed the real data's current state directly:
`V00400` is `MUST_PAY`/`P0` again (Sarath's own manual fix at 16:42,
audit-logged as a normal `ui_edit`), and both `backend/db/app.db` and
`data/Vendor's Details.xlsx` mtimes are consistent with "nothing from
this task touched them again."

**Cleanup note**: my own guardrail-verification probe left two harmless
throwaway files in the real project folder that my sandbox couldn't
delete (a permissions quirk on this mounted folder, not a code issue) —
`backend/_tmp_guardrail_probe_test.py` and `data/_dummy_probe.xlsx`, both
inert (the probe test already ran, the dummy Excel file is a 1-byte
placeholder). Safe to delete either directly from the real machine
whenever convenient; not left in place on purpose.

## AI column-mapping: catch transport-level failures too, not just Gemini API errors

Real bug: a TLS handshake killed by a proxy/firewall produces
`httpx.ConnectError` — never reaches Gemini at all, so it's not a
`genai_errors.APIError`. `ai_column_mapper.py::map_columns()` only caught
the latter, so a connection-level failure fell through uncaught and
crashed the whole upload as a raw 500 instead of the existing clean 503
path (`AIMappingUnavailableError`). Fixed: now catches
`(genai_errors.APIError, httpx.HTTPError)` — the latter is the base class
covering connect/timeout/TLS failures too, same "outage, try again"
message either way.

**Independently verified (this session)**: read the fix — matches
exactly. Reproduced the actual bug scenario myself directly (not just
trusting the report): monkeypatched `gemini_client.generate_text` to
raise a real `httpx.ConnectError`, called `map_columns()` — cleanly
caught as `AIMappingUnavailableError` with the expected message, confirms
the fix works as claimed. Ran the full `test_ai_column_mapper.py` myself
(had to work around this sandbox's per-test slowness on real-file
parsing, ~16 tests took several targeted runs): 12 passed / 4 failed,
matching the reported count exactly, and the 4 failures are genuinely the
same pre-existing real-header-drift pattern (confirmed one directly:
`test_ai_overrides_a_stale_coincidental_header_match` fails on `column
'Status' not found` — a fixture assuming a header the current real sheet
doesn't have, unrelated to this fix). **One gap worth flagging**: I could
not find an actual persisted regression test for this specific
httpx.ConnectError scenario anywhere in the test file or the wider
backend (grepped for `ConnectError`/`HTTPError` — zero test references) —
the report's "verified: a mocked httpx.ConnectError now gets caught
cleanly" claim appears to describe an ad-hoc manual check, not a test
that will guard against a regression here going forward. The fix itself
is real and I proved it works myself, but there's no permanent test
covering it yet.

## Merge Month-end rollover into Upload (this task) — done, separate button retired

Confirmed with Sarath: Finance shouldn't have to remember a separate
"Month-end rollover" button — the smart new-month-detection logic already
existed (`rollover_month()`), it just only ran when that button was
clicked. Folded into every upload instead, so Finance never has to
remember which button to click:

- `backend/month_end/rollover.py` reduced to one extracted function,
  `_reset_vendor_cycle_state(session)` (the same payment_status/
  paid_so_far_this_month/reconsider/override_amount/week_distribution_plan/
  finalized_budget_amount reset loop, unchanged logic, does not commit).
  `rollover_month()` itself, the `if __name__ == "__main__"` block, and
  its own `func`/`load`/`EXCEL_PATH` imports are gone — nothing but
  `commit_upload()` needs this any more (confirmed by grep — only its own
  test file and the router called it, both now removed too).
- `backend/api/routers/rollover.py` deleted outright, `main.py`'s import/
  `include_router` call removed. `/rollover` no longer exists as a route.
- `backend/ingestion/upload.py::commit_upload()` captures the DB's latest
  ledger month before its own `load()` call and compares it to the
  sheet's latest month after — only on a genuine month advance does it
  call `_reset_vendor_cycle_state()`, in the same transaction as the
  ingestion. Unlike the old standalone button, a same-month correction
  re-upload (now the routine case, since every re-upload takes this one
  path) silently skips the reset and never raises — that refusal framing
  only made sense when a separate button existed for Finance to click by
  mistake. The very first upload ever (no prior month to compare against)
  also just skips, no error. `commit-upload`'s response gained
  `cycle_reset`/`vendors_reset` (declared up front on
  `MasterDataCommitResponse` — avoiding the same silent-strip bug class
  hit twice before), surfaced in the upload toast only when a reset
  actually happened.
- Frontend: `MainTab.tsx`'s rollover button, its `ConfirmModal`,
  `showRolloverConfirm`/`isRollingOver` state, `handleRollover()`, and the
  `CalendarClock` import all removed; `api/rollover.ts`/`RolloverResult`
  type deleted (nothing else referenced them). The upload success toast
  appends "New payment cycle started — N vendor(s) reset for the new
  month." only when `cycle_reset` is true — the ordinary same-month case
  stays exactly as quiet as it always was.
- **Tests**: `backend/month_end/test_rollover.py` deleted (tested the
  now-removed `rollover_month()` directly); its 4 scenarios moved into
  `backend/ingestion/test_upload.py` against `commit_upload()` instead —
  advancing-month reset (+ transaction-rollback-together proof on a
  simulated mid-reset failure), same-month correction (no reset, no
  raise), and a new first-upload-ever case. `test_api.py`'s `/rollover`
  route test removed too. Frontend: `MainTab.test.tsx`'s rollover mock
  removed, 2 new tests for the toast wording.
- **Real-data proof**: a real HTTP round trip (`TestClient` against
  `POST /master-data/commit-upload`, temp DB, real master Excel) — first
  upload ever: `cycle_reset=False, vendors_reset=0`; same-month
  re-upload: `cycle_reset=False, vendors_reset=0`; upload with a
  synthetic new month appended: `cycle_reset=True, vendors_reset=422`.
  All three 200, none raised.
- Test counts: `backend/ingestion/test_upload.py` 13 -> 17 (4 new, all
  pass); full `backend/api/test_api.py` unaffected (35 passed/23 failed
  both before and after, identical known dataset-drift failure set).
  Frontend: 12 -> 14 tests, all pass; `tsc --noEmit`/`vite build` clean.

**Independently verified (this session)**: read `rollover.py` — reduced
to exactly `_reset_vendor_cycle_state()`, matches the report. Grepped the
whole backend for `rollover_month`/`/rollover` — zero remaining callers
or routes, only docstring/comment mentions of the module name; confirmed
`backend/month_end/test_rollover.py` and the `/rollover` router are
genuinely gone, not just deprecated. Confirmed `upload.py`'s
`pre_month`/`post_month` capture and `cycle_reset` logic matches exactly,
and that `cycle_reset`/`vendors_reset` are declared on
`MasterDataCommitResponse` up front. Confirmed `MainTab.tsx`'s rollover
button/modal/state/`CalendarClock` import and `api/rollover.ts` are fully
removed. Ran 3 of the 4 new `test_upload.py` tests directly:
`test_commit_upload_resets_cycle_state_on_genuine_new_month`,
`test_commit_upload_same_month_correction_does_not_reset_or_error`, and
`test_commit_upload_first_upload_ever_does_not_reset_or_error` all pass
(the first upload's own real-file ingestion took ~15s alone — this
directory's tests are genuinely slow against the real ~451-vendor file,
not just a sandbox quirk). Could not get
`test_commit_upload_reset_failure_rolls_back_ingestion_too` to complete
within this sandbox's tool timeout on its own — consistent with the same
atomicity guarantee `rollover_month()` already had proven before this
task relocated it, nothing suggests a regression, just an unconfirmed
completion here. Confirmed `test_api.py` and `backend/month_end/` have no
remaining rollover references at all.

## Vendor edit UI — auto-update Category<->Priority Tag, model plans off it (this task)

Confirmed with Sarath: editing Category or Priority Tag individually
through the UI now auto-updates the sibling field (reusing
`column_mapping.derive_category_and_priority_tag()`/
`PRIORITY_TAG_TO_CATEGORY` — no duplicate mapping tables), writes both to
their own Excel columns in one workbook load/save cycle
(`_stage_field_writes()`, replacing the old single-field-only staging —
a real bug caught and fixed along the way: writing two fields via two
separate save cycles would have silently discarded the first), and logs
the sibling change as its own distinct `AuditLog` entry
(`ChangeSource.UI_EDIT_DERIVED`, new enum member) so it's never
indistinguishable from a direct Finance edit. Commitment/
`commitment_months` edge case: non-blocking warning only (not a hard
requirement) — `commitment_months` defaults to `1` in the DB for every
vendor regardless of whether Finance ever reviewed it, so there's no way
to cleanly require it only when genuinely unset; matches this codebase's
existing warn-don't-block house style.

**Independently verified (this session)**: read `vendor_edits.py`'s
`_derive_sibling()`/`VendorFieldEditResult`/`_stage_field_writes()`/
`_save_workbook_to_temp()` and `enums.py`'s new `UI_EDIT_DERIVED` member —
all match the report exactly. Ran the new
`test_category_priority_auto_update.py` myself: 17/17 pass, including
`test_generate_plan_before_after_category_edit_changes_treatment`, which
proves the model itself plans differently after such an edit (a Normal/P2
vendor under a real shortfall starts bucket-ceiling-cut, not guaranteed;
after deriving Commitment via a Priority Tag edit, the same vendor is
`GUARANTEED` at exactly its required amount — under a **different**
formula, confirmed by using a distinct `commitment_months` so the two
figures can't coincidentally match, a subtlety the report caught and
fixed in itself before I even reviewed it). The 22 pre-existing failures
in the broader suite were spot-checked against the report's tracing —
consistent with known, already-documented causes (hardcoded
`header_row=3` in `test_vendor_edits.py`'s own helpers, shared-session
audit-log-count pollution across parametrize cases, and `app.db` still
showing 100% `category=Normal` since the ingestion-time fix only affects
future re-ingestion, not the already-persisted file) — nothing new here.

## Master Grid — hide until real data exists (this task)

Follow-up to the Main-tab revamp: the grid was rendering unconditionally,
including before any master file was ever uploaded — `build_master_grid()`
threw a raw, unhandled `FileNotFoundError` (real 500) in that state.
Fixed: `grid.py` returns a `_NO_DATA_GRID` sentinel
(`{columns:[], vendors:[], extra_field_widgets:{}, has_data:false}`) when
either zero active vendors exist yet (checked first, cheapest, no
workbook open needed) or the workbook file itself is missing (backstop for
a vendor-rows-exist/file-since-deleted desync). Signal chosen deliberately:
active vendor count, not file existence — a stale file could sit at
`EXCEL_PATH` with an empty DB, or vendor rows could outlive a deleted
file; vendor count is what actually answers "did an upload really land."
Frontend (`MasterDataGrid.tsx`) shows a plain "Upload a file to see your
master data here" placeholder when `has_data` is false — judged on that
flag specifically, not `vendors.length === 0` (which is also the ordinary
"every vendor deactivated" shape that must still render the real,
empty-bodied table).

**Independently verified (this session)**: read `grid.py` — the
`_NO_DATA_GRID` sentinel, the vendor-count short-circuit, and the
`FileNotFoundError` backstop all match the report exactly; real success
path now returns `has_data: True` too. Read `MasterDataGrid.tsx` — the
`has_data`-gated placeholder branch matches exactly, correctly judged on
the flag rather than an empty vendor list. Ran the 2 new backend tests
directly: both pass. Confirmed live against the real `app.db` (currently
422 active vendors) that the ordinary success path still returns real
data with `has_data: true` implied.

## Main-tab revamp (this task) — upload confirm modal, 50/50 layout, real Master Grid

- **Upload confirm modal**: picking/dropping a file now opens a dedicated
  `UploadConfirmModal.tsx` (not a `ConfirmModal.tsx` extension — its
  title/message/confirm/cancel shape had no room for two input fields)
  showing the file name and a replace-current-data warning, with Planning
  Month (primary, required) and Sheet start month (secondary, "auto-
  detected — override only if wrong") fields. Confirm & Upload calls
  `commitUpload()` with both; Cancel discards the picked file, no request
  made. `MainTab.tsx`'s upload card itself is now just drag-drop + "Upload
  Excel" — the sheet-start-month field that used to live on the card moved
  into the modal.
- **Planning Month persistence**: new `backend/shared/planning_month_store.py`
  (Config-backed `get_current_planning_month()`/`set_current_planning_month()`,
  same pattern as `column_mapping_store.py`'s sheet-start-month functions).
  `commit-upload` accepts an optional `planning_month` field and persists it;
  `new_model_2.py`'s `_resolve_planning_month()` falls back to it (after the
  latest `PlanRun`, before raising); new `GET /models/5/current-planning-month`
  exposes the resolved value read-only. `GET /models/5/minimum-funds-required`'s
  `planning_month` param is now optional, resolved the same way — confirmed
  the one existing frontend caller (`PlanningView.tsx`'s
  `handleCalcFundsRequired`) always passes it explicitly, so this is
  additive, not a behavior change for that caller.
- **Planning tab**: no longer asks Finance to pick a planning month —
  `PlanningView.tsx`'s Planning Month card is now always read-only,
  refetched from `GET /models/5/current-planning-month` on every
  `refreshSignal` bump (a fresh upload's confirmed month shows up without a
  reload). Changing the planning month mid-cycle (edge case, flagged, not
  built): today the only path is re-uploading through the confirm modal
  with a different month — no separate "change" affordance was added.
- **Master Grid wired in**: `MasterDataGrid.tsx` compiled cleanly against
  the current `types.ts`/`api/masterData.ts`/`GET /master-data/grid` shape
  with no drift found — imported and rendered on the Main tab, full width,
  below a new 50/50 upload-card/upload-history row (single column on
  narrow viewports). Polished per Sarath's ask: sticky header row, frozen
  ERP Code + Vendor Name columns (fixed pixel widths so the sticky `left`
  offsets are predictable), zebra striping + row hover, and a lightweight
  vendor-name/ERP-code text filter (client-side, low lift). Existing
  editable-passthrough-column behavior (`patchExtraField` dropdowns/inputs)
  untouched. Its own `refreshSignal` is scoped to `MainTab.tsx` (bumped on
  upload/revert, and rollover at the time this was written — rollover's own
  separate action was retired in the merge-rollover-into-upload task below),
  separate from `App.tsx`'s `refreshSignal` (which still only drives
  `PlanningView.tsx`).
- **Tests**: backend — `commit-upload` persists `planning_month` (readable
  back via `current-planning-month`), a bad-format `planning_month` is 400,
  the new `sheet-start-month` GET endpoint, `minimum-funds-required` with no
  `planning_month` 400s when nothing's resolvable yet and resolves from the
  persisted config value once one exists, `current-planning-month`'s
  fallback order (persisted config, then latest `PlanRun` once one exists),
  and Generate Plan falling back to the persisted value with nothing
  explicit in the request body. Frontend — no test runner existed in this
  repo before this task; added a minimal `vitest` + React Testing Library
  setup (`vite.config.ts`'s `test` block, `src/test/setup.ts`, `npm test`)
  since none of this task's own explicitly-requested modal tests were
  otherwise possible. 9 new frontend tests (`UploadConfirmModal.test.tsx`,
  `MainTab.test.tsx`) all pass; `tsc --noEmit` clean.
- `.env` fixed to point `PAYMENTS_DB_PATH` at the real seeded `app.db` by
  default instead of the intentionally-empty `demo.db`.
- Git checkpoint commit made (`6abd59d`) — working tree was 98+ files deep
  in uncommitted/untracked changes before this; there's now a real restore
  point.

## Demo prep — P1 done, P2/P3 prompts written, not yet run

Regrouped for the leadership-demo push, after a targeted read-only audit of
data ingestion/aging/min-funds/weekly-distribution/overrides/status
changes/finalize/pay+comment against the newly-received real Excel sheet.

**P1 — must fix, real/silent-wrong-data risk** (`claude_code_prompt_P1_demo_readiness.md`) — **all 4 done**, see `CLAUDE.md`:
1. ~~`SHEET_START_MONTH` hardcoded in `column_mapping.py`~~ — **Done.** Moved
   to the `config` table (`column_mapping_store.get_/set_sheet_start_month()`,
   seeded with the same `2025-04-01` default); `build_sheet_map()` takes an
   explicit `sheet_start_month` param instead of reading a global. Added
   `sheet_start_month_warning()` (non-blocking, fires on 2+ header/position
   disagreements, not the sheet's one confirmed typo alone) surfaced on
   `commit-upload`'s `ai_column_mapping_messages` banner. Stretch goal done
   too: `commit-upload` takes an optional `sheet_start_month` override
   (`MainTab.tsx` input), persisted for future uploads.
2. ~~`header_overrides` not threaded through `vendor_edits.py`/`grid.py`~~ —
   **Done.** Both now load persisted overrides before building a sheet map.
   New test: `backend/ingestion/test_header_override_threading.py`. Also
   found and fixed a related pre-existing gap: `MasterDataCommitResponse`
   was missing its own `ai_column_mapping_messages` field, so FastAPI's
   `response_model` was silently dropping that banner (and this task's new
   warning) before it ever reached the frontend.
3. ~~Planning-table-vs-Weekly-view Min Funds discrepancy~~ — **Done.**
   `planner.py`'s weekly-view builder now calls `required_amount_v2()`;
   confirmed end-to-end (Planning table and Weekly view agree for the same
   vendor), `test_planner.py` reworked (4 assertions), full suite back to
   baseline's 47 failed/256 passed (all pre-existing dataset-drift, no new
   failures), frontend re-confirmed to need zero changes. `CLAUDE.md` and
   `docs/14-new-model-2.md` updated to reflect this as resolved.
4. ~~Refresh `docs/16-endpoint-ui-mapping.md`~~ — **Done.** Re-grepped all
   live routes: still the same 35 endpoints, same UI wiring as documented;
   only the doc's own summary line ("34/34") was stale by one, fixed.

**P2 — should fix, visible polish, moderate effort** (`claude_code_prompt_P2_demo_polish.md`) — **all 5 done**:
5. ~~Vendor payment-history view~~ — **Done.** `getVendorPayments()` +
   `Payment` type + `formatDateShort()`; new "Payment history" table in
   `VendorDetailModal.tsx` (date/amount/note, newest-first), gated behind
   the same "view-only during Pay flow" convention Aging/Min Funds already
   use. Verified against real data: `GET /vendors/1/payments` → `[]`,
   confirmed as a real empty state.
6. ~~Audit Log viewer~~ — **Done.** New `api/auditLog.ts` +
   `AuditLogEntry` type; table added to the Configuration tab below the
   bucket table, own independent load/error state, most-recent-first.
   Verified against real data: 2 real rows render (a Status field edit).
7. ~~`weeksInMonth` race condition fix~~ — **Done.**
   `useState(5)` → `useState<number | null>(null)`; the Assigned Week
   `<select>` now only ever shows the full range once `weeksInMonth`
   actually resolves, never a guessed range. `tsc --noEmit` clean.
8. ~~Remove dead `finalizePlan()`~~ — **Done.** Removed from `vendors.ts`;
   confirmed the backend endpoint's only other caller is
   `backend/api/test_api.py` directly (`test_ui.html` uses
   `/models/5/finalize` instead) — backend endpoint itself left untouched.
9. ~~Serve `VendorCategory` from a backend endpoint~~ — **Done.**
   `GET /vendors/categories` (registered before the dynamic
   `/vendors/{vendor_id}` route — confirmed correct static-before-dynamic
   order), sourced from `shared/enums.py::VendorCategory` +
   `column_mapping.CATEGORY_EXCEL_LABEL`. `PlanningView.tsx`'s 4 render
   sites (filter chips, dropdown, sort order, payment-tracking badge) now
   all read from it; `CATEGORY_OPTIONS`/`CATEGORY_LABEL` kept only as a
   same-session bootstrap default (no race risk — unlike `weeksInMonth`,
   this set isn't month-dependent). New backend test confirms the endpoint
   matches the enum exactly.

Verified independently (this session): route registration order, all new
frontend wiring (imports/usages), dead-code removal, and 23 targeted
backend tests (categories/payments/audit-log) — all pass. A representative
unrelated slice (`weekly_planning`/`models`, 50 tests) also run clean:
1 failure, same known dataset-drift pattern (query assumes a vendor with
`opening_balance > 500,000` exists in the 10-vendor real seed — pre-existing,
unrelated to this task). Full 264-test suite not independently re-run in
this session (sandbox time constraints), but nothing checked contradicts
the reported 264 passed/47 failed, identical failure set.

**P3 — backlog, low demo risk** (`claude_code_prompt_P3_backlog.md`) — **both done**:
10. ~~Rollover UI~~ — **Done.** `postRollover()` (`api/rollover.ts`),
    `RolloverResult` type, "Month-end rollover" button in `MainTab.tsx`
    next to "Revert to previous upload," gated behind a danger-styled
    confirm modal naming the exact fields that reset. The same-month-
    correction 400 is matched on the backend's own stable substring
    ("the re-ingested data itself has still been saved") and shown as a
    warning toast (still triggers a grid refresh, since the re-ingested
    data did save) rather than an error. Verified end-to-end against the
    real backend, not just reasoned about: POSTed the frontend's exact
    body and got the exact 400 substring back. `rollover_month()` itself
    untouched.
11. ~~Stale "Model 1" comment cleanup~~ — **Done.** Fixed the named
    `plan_allocations.py` docstring plus 5 more with the same stale
    present-tense pattern, found via grep:
    `plan_allocations.py::patch_week_distribution`, `db/models.py`
    (`finalized_budget_amount`, `score`/`current_aging_bucket`,
    `PlanAllocation.override_amount`), `shared/aging.py`'s module
    docstring. Deliberately left `scoring.py`'s DB config keys
    (`model1.weight.*`) alone — a real rename, not a comment fix, correctly
    flagged as out of scope for a comment-cleanup task.

Verified independently (this session): `rollover.ts`/`MainTab.tsx` wiring,
the exact backend 400 substring match, and all 6 comment fixes (grepped —
none of the flagged stale phrasing remains). Ran `backend/shared` (84
tests: 4 failed, same known dataset-drift pattern — `assert 9 == 83`-style
failures from the 83-vendor-authored tests against the 10-vendor real
seed) and a rollover/override/allocation slice of `test_api.py` (15 tests:
2 failed, same pattern — test math assumes larger opening balances than
the real seed has). Nothing contradicts "zero new regressions." Could not
verify `tsc --noEmit` myself (sandbox's frontend toolchain isn't runnable
here), but the reported comment/file diffs are all backend/comment-only
plus additive frontend files, low risk.

**Generic Config UI** (scoring weights, `GET /config`) — still deferred,
no prompt written; revisit only if those weights ever need to be
Finance-editable.

7. ~~**Within-week execution order**~~ — **Done.** `planner.py` now ranks
   by priority tag first (P0, P1, then the live `priority_buckets` order),
   score descending only as a tiebreak within the same tag; untagged
   vendors rank last (flagged default, no spec coverage for this case).
   Test rewritten (`test_within_week_order_orders_by_priority_tag_then_score`)
   and confirmed against the real seeded data (no synthetic tagging
   needed — the pull-forward already collapses P0/P1/P2×2/P3×2/P4/untagged
   into one displayed week). Full suite: 47 failed/263 passed, identical
   failing set before/after.

**AI-first column mapping** (`claude_code_prompt_ai_first_column_mapping.md`) — **Done**:
- `ai_column_mapper.py`'s `resolve_column_mapping()` now calls the AI layer
  on every upload for every field in `ALL_MAPPABLE_FIELDS`, not just when
  something's missing (a deliberate reversal of the previous gap-filler-only
  design — module docstring rewritten to record this). Also now reads row 2
  (the merged/master header block), not just row 3, as extra evidence.
  A field with an existing candidate (fixed default or persisted override)
  can now genuinely be overridden by the AI, but only on high confidence +
  a real, different column — logged loudly (`AI OVERRODE...` banner/audit
  entry), never silent; medium/low disagreement stays a warning only, same
  as before.
- Real, non-mocked verification run against 2 synthetic messy fixtures
  (`backend/ingestion/test_fixtures/messy_sheet_1.xlsx`/`_2.xlsx`, scrambled
  column order + renamed/confusable headers) via
  `verify_ai_first_mapping_manually.py`: `priority_tag` vs. `assigned_week`
  — the specific confusion Sarath flagged — resolved correctly, high
  confidence, both sheets, every run. `assigned_week` itself turned out
  less reliable against two OTHER look-alike columns already in the real
  sheet (a legacy "Assigned Week Order" column, and the W1-W5 per-week
  distribution block) — tightened `assigned_week`'s `FIELD_DESCRIPTIONS`
  entry to rule out the first; the second (W1-W5 block) is a real,
  still-open risk, flagged below, deliberately not fixed here (would have
  meant teaching the AI the monthly-block structure — explicitly out of
  scope, see the prompt's own section 5).
- Found and fixed a real gap of its own along the way: making the AI call
  unconditional meant every test exercising `commit_upload()` without its
  own mock would have started making real, live Gemini calls. Added
  `backend/ingestion/conftest.py` + `backend/api/conftest.py` (autouse
  stub, "confirm everything, propose nothing"), deliberately scoped to
  just those two directories — a first, suite-wide attempt broke
  `test_talking_script.py`'s two tests that deliberately exercise
  `gemini_client`'s real missing-API-key error.
- Test counts (reported): `test_ai_column_mapper.py` 10 → 13 (3 rewritten,
  3 new); full suite 47 failed / 264 → 267 passed, identical failing set.
  Independently re-verified in this session: `ai_column_mapper.py`'s own
  precedence logic read end-to-end and matches the design exactly; both
  fixtures, the manual script, and the `docs/07` update all present;
  11 of 13 tests in that file pass on this sandbox's Python 3.10 — the
  other 2 fail here only because they use `x in SomeStrEnum`, whose
  membership-check semantics changed in Python 3.12 (pre-3.12 raises
  `TypeError` for a plain string; 3.12+ treats it as a value check). This
  project's own bytecode cache shows the real dev machine runs Python
  3.14, where this is a non-issue — not a real bug, just a **portability
  landmine worth knowing about** if this ever runs on an older Python
  (e.g. a CI image pinned to 3.10/3.11). A representative slice of
  `backend/ingestion` (17 failures) checked by hand — all the same
  established dataset-drift pattern (hardcoded old-sheet vendor codes like
  `V00400`, or a query assuming a vendor above a balance threshold that
  doesn't exist in the 10-vendor real seed), not new regressions.
- **Flagged, not yet a task**: the real sheet has three columns that all
  read as "week-related" — `assigned_week` (the one this task cares
  about), the W1-W5 per-week distribution block, and a legacy "Assigned
  Week Order" column — and only `assigned_week` is meant to be AI-resolved
  today. Worth its own future task if the monthly Payable/Payment/week
  block structure ever needs the same scrambled-column resilience.

## Not yet scoped

8. **Analytics tab** — placeholder only; waiting on your reference for what
   it should show.
9. **RBAC / real auth** — currently a mocked placeholder user/role
   (`PLACEHOLDER_CURRENT_USER`). No role/permission model exists in the DB
   shape yet.
10. ~~Monthly Payable/Payment/week block detection~~ — **Resolved (header-row
    detection task)**: confirmed this was already correct, not a gap —
    Payable/Payment blocks are derived purely from position relative to the
    *resolved* `total_payable_col`/`total_payment_col` (columns between the
    previous anchor and `total_payable_col` = Payable; between
    `total_payable_col` and `total_payment_col` = Payment), exactly Sarath's
    stated rule, never a fixed count. This was never meant to be AI-resolved
    column-by-column — the actual gap this task fixed was one layer up: the
    HEADER ROW itself was hardcoded (`HEADER_ROW`/`DATA_START_ROW` = 3/4),
    so on a sheet with a different header-row position those "resolved"
    anchor columns were being searched for in the wrong row entirely. See
    `docs/07-data-pipeline-and-master-sheet.md`'s new "Header row position"
    section.

## Header-row detection (this task) — done, one new gap found and flagged

`column_mapping.detect_header_row()`/`resolve_header_row()` replace the
hardcoded `HEADER_ROW`/`DATA_START_ROW` constants — detected by content
(text/numeric shape + column coverage) every upload, confirmed/correctable
by the same single AI call `ai_column_mapper.py` already makes for column
mapping, never persisted. A final sanity gate
(`column_mapping.validate_sheet_layout()`) runs right before any DB write as
a last-resort backstop. See `docs/07`/`docs/9-open-questions.md` for detail.

**New gap found, explicitly out of scope for this task, flagged in
`docs/9-open-questions.md`**: the second real Finance file
(`data/Finance payments excel.xlsx`) has no W1-W5 weekly-breakdown block at
all (only a single "Week" tag column) — `build_sheet_map()`'s pre-existing
`week_cols` requirement correctly rejects it (`"No W<n> week columns
found"`), independent of the header-row fix. Confirmed by directly
resolving header row against this real file (`row 1`, `score=0.99`,
`confident=True`) — the header-row problem is fully fixed; this is the next
blocker after it, not a symptom of it.

**Environment note, not a code regression**: earlier in the same debugging
session, `backend/db/app.db` was intentionally reset to empty (an explicit
"give me a fresh system" request) and `data/Vendor's Details.xlsx` — the
local, git-ignored fixture ~40 existing tests copy via `EXCEL_PATH` — was
overwritten with the above incomplete-shape real file (as a side effect of
testing the upload endpoint). Together these make the vast majority of the
full suite fail/error right now (132 failed/76 errors, vs. the previously
documented 47 failed/267 passed baseline) — traced by hand to exactly these
two causes (empty seeded DB; missing week-columns in the copied fixture),
not to this task's diff. This task's own correctness is verified instead
via 13 new, self-contained unit tests (fixtures untouched by the above) plus
direct verification against the real file. Restoring a seeded `app.db` and
resolving the W1-W5-block gap above would restore a meaningful full-suite
baseline for future tasks.

**Independently verified (this session)**: `detect_header_row()`/
`resolve_header_row()`/`AmbiguousHeaderRowError`/`validate_sheet_layout()`/
`SheetLayoutError` all read exactly as described; `grid.py` (line 41 — a
literal `row=3`, not even via the old constant, confirming it was missed by
the earlier `header_overrides` threading pass) and
`backend/configuration/vendor_edits.py` both genuinely thread
`sheet_map.header_row` through now. Re-ran `detect_header_row()`/
`resolve_header_row()` directly against `data/Finance payments excel.xlsx`
myself: row 1, score 0.99, confident — then `build_sheet_map()` fails with
the exact same `"No W<n> week columns found"`, confirming the real-file
result verbatim. Confirmed `app.db` genuinely has 0 vendor rows, and
`data/Vendor's Details.xlsx`/`data/Finance payments excel.xlsx` are
byte-identical in size (163,884 bytes) — consistent with the claimed
overwrite. All 11 `test_header_row_detection.py` tests pass in isolation.
One correction to the report's own claim: of the "13 new tests, all pass in
isolation," the 2 added to `test_ai_column_mapper.py` (not
`test_header_row_detection.py`) actually **fail** in this environment right
now — same root cause as everything else in this section
(`_fresh_db_and_master()` copies the corrupted `Vendor's Details.xlsx`
fixture, which lacks week columns) — not a defect in the fix itself, just
an overstated claim in an otherwise accurate report.

## First real end-to-end upload — bugs found and fixed live, in order

Sarath uploaded the real Finance file through the running app for the
first time. In order, found and fixed: (1) `commit_upload()` crashing with
no prior master file to back up on a fresh environment — fixed, skip
backup when none exists; (2) the header-row detection task above; (3) the
W1-W5 week-columns requirement made optional (system-generated output
per `docs/07`, never a required Finance input — `week_cols=[]`/
`week_total_col=None` when absent, seeding step skipped, not an error);
(4) `parse_assigned_week_order()`'s legacy packed-column parser made
tolerant of this file's plain `"W2"`-style tag instead of hard-raising;
(5) the same `"W2"`-style tag tripping a second, separate strict
`int()` cast in `load_excel.py:206` for `assigned_week` itself — fixed to
parse both a bare int and a `"W<n>"` tag, reusing the existing week-number
parsing logic.

Upload now succeeds end-to-end: 422 vendors, aging/outstanding
calculating correctly.

**Highest-priority bug found next, fixed**: `generate_plan()` crashed with
`KeyError: 'P0'` — real root cause confirmed (not guessed): the guaranteed/
normal split was category-only, but 393 of 422 real vendors have
`category=NORMAL` with `priority_tag` P0/P1 — CLAUDE.md rule 5 defines
"guaranteed" by tag, not category. Those vendors fell into the
bucket-ceiling pool, where `bucket_pct` correctly has no P0/P1 key (rule 5
says those buckets should never need to exist) → crash. Fixed:
`_is_guaranteed()` now checks category OR tag; category-based guaranteed
behavior for untagged Must Pay/Commitment vendors is unchanged, additive
only.

**Independently verified (this session)**: read the `_is_guaranteed()`
diff and confirmed line 334 (`bucket_pct[r["effective_bucket"]]`) only
ever iterates the `normal` list now, never `guaranteed` — a P0/P1-tagged
vendor structurally cannot reach that lookup anymore. Ran `generate_plan()`
directly against the real seeded `app.db`: 421 rows, no crash, 390
guaranteed at exactly 100% of `required_amount`, 2 (`V00986`, `V00393`)
clamped to `outstanding_balance` (rule-1 ledger clamp, correct — confirmed
no P0/P1 vendor is underfunded relative to what they actually owe). Ran
`test_allocator.py` myself: 28 passed / 6 failed, identical to the
report's claim — spot-checked one failure
(`test_absolute_funds_gives_100_percent_not_ceiling`) and confirmed it's
exactly the claimed pre-existing rounding artifact (a few rupees between
`required_amount`/`outstanding_balance`, inside the code's own
`MONEY_EPSILON=1.0` but outside the test's stricter `1e-6`), not a new
regression. Minor inconsistency in the report itself: the test-failure
section names "V00979, V00262" as the affected vendors, but the actual
run's warnings/my own check show it's the same `V00986`/`V00393` from the
real-data check — a copy/paste slip, not a substantive error.

**Not fixed yet, explicitly deferred**:
- Vendor payment-history card not showing prior history in the UI —
  lowest priority, queued.
- `test_allocator.py`'s 6 failing tests still encode the old
  category-only guaranteed logic and a too-strict tolerance — need
  updating to match the new tag-or-category rule and the real data's
  rounding reality, not yet done.

**(6) Gemini outage handling** — a real Google-side 503 ("high demand")
during a live upload surfaced as an unhandled 500 (`google.genai.errors.
ServerError` had no handler). Decision: fail loudly with a clean error,
never silently fall back to deterministic-only column mapping — that
fallback would reintroduce the exact wrong-silent-data risk this session
already spent significant effort eliminating. Fixed: new
`AIMappingUnavailableError` (`ai_column_mapper.py`), raised from
`map_columns()`'s one `generate_text()` call site on `genai_errors.
APIError` (the missing-API-key `ValueError` path is left untouched,
already distinct); new `main.py` handler returns a clean 503. Retry policy
itself was checked, not widened — the `google-genai` SDK already retries
5 times with jittered exponential backoff (1s-60s cap) internally before
raising, which is reasonable as-is.

**(7) "Funds Left" card fixed** — was bound to `funds_left_for_regeneration`
(a pre-allocation, override-adjusted figure, always == Expected Funds with
zero overrides), not the real post-allocation leftover. Real fix turned
out to be two-part: `allocator.py`'s own `leftover_remaining` was already
being silently stripped from the real HTTP response — `new_model_2.py`'s
`NewModel2GeneratePlanResponse` schema never declared it, same failure
class as the earlier `ai_column_mapping_messages` gap. Added
`leftover_remaining: float` to the schema, rebound the frontend card to
`data.plan.leftover_remaining`, left `funds_left_for_regeneration`
untouched (still correctly serves regeneration).

**Independently verified (this session)**: copied `app.db` to a clean path
and made a real HTTP call myself (`POST /models/5/generate-plan-and-weekly-view`
with Expected Funds == Min Funds Required, matching Sarath's exact
scenario): `leftover_remaining` present in the response, value ≈ 0, exactly
as expected; `funds_left_for_regeneration` unchanged. Confirms both the
"was it really being stripped" claim and the fix itself.

**Bug 2 (Finalize warns after regeneration with no override) — verdict:
working as designed, not a bug.** 391 of 421 real vendors are P0/P1
(Must Pay/Commitment), and rule 5 requires them to always be paid in full
— when their combined required amount alone exceeds the Expected Funds
figure entered, Finalize correctly reports a real shortfall. Confirmed
with Sarath: this is intentional — Min Funds Required is a flat sum
across every vendor regardless of tag, funding it in full correctly gets
everyone to 100% (proven by Bug 1's own scenario), and only an
under-funded run pushes the whole shortfall onto Normal/Inactive (never
P0/P1, by design). No code change needed here.

**(8) `required_amount_v2()` could return more than a vendor's real live
balance** — found while investigating why vendor `V00393` showed a
confusing 49.47% allocation. Real root cause: `build_vendor_tranches()`
(`aging.py`, shared infra, untouched here) only ever creates a tranche for
a positive payable row and drops any negative-payable credit/correction or
sub-`MONEY_EPSILON` rounding drift as noise instead of netting it — so the
tranche list can disagree with the vendor's real, ledger-reconciled
balance. Not a documented/anticipated data pattern (`docs/07`'s "known
slip" is a different, specific case) — a genuine, previously-unflagged
gap in the tranche builder. Fixed defensively at the two functions that
actually hand this number to Finance/the allocator, same principle as
`allocator.py`'s own rule-1 clamp: `_compute_v2()`'s 6 return sites all
now capped at `min(total, live_outstanding)`. `V00393`: 16,006.66 ->
7,917.97 (now shows 100%, no clamp). `V00986`: 244,302.64 -> 244,299.44.
Min Funds Required total: 86,898,233.68 -> 86,890,141.34.

**Independently verified (this session)**: read the `_compute_v2()` diff
— all 6 return paths wrapped in `_capped()`, Commitment branch correctly
left untouched. Ran `required_amount_v2()` directly against a copy of the
real DB for both vendors: `V00393` -> 7,917.97, `V00986` -> 244,299.4409,
total -> 86,890,141.3441 — matches the report exactly. Ran
`test_min_funds_v2.py` myself: 19/19 pass, matches the claimed fix count.
`build_vendor_tranches()` itself intentionally left untouched (shared by
aging/Model 1 too, correctly flagged as a bigger, separate change).

**(9) Fresh-environment upload round — DB confirmed genuinely populated,
plus 5 real UI bugs found and fixed.** After the clean-slate reset above,
Sarath did a real first upload against the empty DB — ingestion itself
worked (422 vendors, 6330 ledger rows), a momentary "data not loaded"
concern turned out to be a stale-frontend-state issue, not lost data
(see bug 1 below). Also caught: `MasterDataGrid.tsx` is dead code (see
correction above) — all 5 fixes below actually landed in
`PlanningView.tsx`, the real reachable grid.

1. Filters popover — wrong colors + clicks not registering: the popover
   sat at a lower z-index than the vendor table's own sticky header
   (`z-30`/`z-40`), so the table was literally painted on top of it and
   intercepting clicks — not a blur-handler bug as first suspected.
   Bumped to `z-50`, restyled to the app's actual card convention
   (`border-gray-200/90`/`shadow-2xs`).
2. Hard-refresh-needed-to-see-new-data: `PlanningView` stays mounted
   across tab switches and never re-fetched after an upload — missing
   refetch signal, not HTTP caching. Added a `refreshSignal` prop
   (same convention `MasterDataGrid.tsx` already had), bumped from
   `App.tsx` on upload.
3. Default sort by priority tag (P0 first) + per-column filters: the sort
   already existed but used a hardcoded `99` fallback for untagged
   vendors instead of the dynamic `len(tag_rank)` rule `planner.py`
   already established — fixed to match. Added click-to-open filters to
   Vendor/ERP code/Assigned Week/Outstanding; deliberately did not
   duplicate filters already covered by the Filters popover (Category/
   V-Priority/Aging Bucket) or the dynamic per-plan-run columns (flagged
   as a separately-scoped future feature, not cut silently).
4. Available Funds input felt janky while typing: a fully-controlled
   comma-formatted input was resetting the caret to string-end on every
   keystroke. Fixed by tracking/restoring caret position by digit count
   instead of string index.
5. "Funds Left" showing "-0": `Math.round(-0.0044997)` produces JS's
   signed `-0`, which `.toLocaleString()` renders as the literal string
   `"-0"` — normalized any zero-rounded result to plain `0` in the shared
   `formatMoney()` helper before formatting.

**Independently verified (this session)**: confirmed the DB was genuinely
populated (422 vendors etc.) before accepting "not loaded" as a real
concern — it wasn't. Read the actual diffs for all 5 fixes: `z-50` vs.
the table's `z-30`/`z-40` confirmed; `refreshSignal` wired through
`PlanningView.tsx`/`App.tsx` confirmed; untagged-fallback now reads
`Object.keys(priorityTagOrder).length` instead of a literal `99`,
confirmed; `formatMoney()`'s `rounded === 0 ? 0 : rounded` correctly
normalizes `-0` (JS: `-0 === 0` is `true`) while leaving real negatives
untouched, confirmed by reading the code (logic checks out, matches
`-0.6 -> "-1"` style claims). Independently confirmed the more important
thing: after this task's own test-upload accidentally contaminated the
clean-slate reset, the recovery was real — re-checked `app.db` (all 10
tables back to 0 rows) and `data/` (no `Vendor's Details.xlsx` present,
both contamination artifacts archived, not deleted) myself. Also
independently confirmed the `MasterDataGrid.tsx` dead-code finding via
grep — genuinely never imported outside its own file and a comment.
`CLAUDE.md` and this doc's own earlier claim about it corrected above.

**(10) Priority tag / Assigned Week "vanishing" after upload — two real
issues found, both fixed.**

1. **Real root cause of the actual symptom**: `test_api.py` had 3
   vendor-PATCH tests missing their own `excel_path` override, so
   `update_vendor_field()` fell back to its default — the REAL
   `EXCEL_PATH` — and wrote straight into the real production master
   Excel during test runs. Confirmed to have actually created a stray
   "Priority Tag" column in the real file (whose genuine header is
   "Prioity") and silently overwritten a real vendor's Category cell —
   a rogue extra column competing with the real one is exactly the kind
   of thing that could make AI/deterministic mapping resolve to the
   wrong place. Fixed all 3 call sites to use their own `tmp_path` copy,
   never the real file, closing this class of bug for good.
2. **A second, real, independent bug also found and fixed**: an
   unresolved optional field (e.g. `priority_tag`/`assigned_week`) on a
   "low"/no-confidence AI answer used to just silently `continue` with
   zero trace anywhere — `load()` would then write `None` for every
   vendor with no existing value, indistinguishable from the sheet
   genuinely having no data there. Now always logged to `audit_log` +
   upload banner, matching the existing "AI disagrees with an
   already-matched field" warning pattern.

Contaminated file preserved (not deleted) as
`Vendor's Details.xlsx.PRE-RESTORE_2026-07-30_contaminated`; real file
restored from a clean backup, DB re-ingested. New regression test
(`test_low_confidence_optional_field_never_silently_vanishes`) added.

**Independently verified (this session)**: read the actual
`ai_column_mapper.py` diff — the low-confidence logging block is exactly
as described. Confirmed the `test_api.py` comment documenting the
contamination bug verbatim ("confirmed to actually create a stray
'Priority Tag' column... the real header is 'Prioity'"). Checked the real
database directly: 422 vendors, 0 vendors with `NULL` `priority_tag`, 0
with `NULL` `assigned_week` — matches the claim exactly. Confirmed the
contaminated file is archived, not deleted, and the restored
`Vendor's Details.xlsx` is back in place. New regression test confirmed
present.

**Full-suite results in**: 327 -> 328 tests (+1, the new regression test),
failures 61 -> 64 (net +3) — all fully accounted for per the report: 1
genuinely fixed (`test_fully_resolved_sheet_still_calls_gemini_once_and_changes_nothing`,
updated stale assumption), 4 "new" failures all traced to the same
pre-existing "Prioity"/"Week" real-header drift (test setup helpers
looking for the literal default header text, which doesn't exist on the
real sheet) — not caused by this fix, just no longer masked by the
file-contamination bug's lucky side effects. Re-verified directly: both
the new regression test and the fixed test pass against a real master
file. Couldn't reproduce the exact full-suite counts in this sandbox
in this pass — `data/Vendor's Details.xlsx` is currently archived here
from the earlier reset, so several fixture-dependent tests fail in this
environment for that reason alone, not a code issue; nothing checked
contradicts the reported numbers. **This task is done.**

**(11) Full permanent wipe (round 2 reset) — done, one real slip caught
and fixed.** Sarath asked for zero retained copies this time (overriding
the earlier archive-don't-delete caution): every `app.db.*` backup/
archive/contamination file, all of `data/archive/`, and the contaminated
Excel artifact permanently deleted. Fresh `app.db` recreated via
`init_db()`, all 10 tables at 0 rows. Backend + frontend restarted clean
(Vite cache cleared too), confirmed via real HTTP calls
(`GET /vendors` -> `200 []`).

**Independently verified (this session)**: confirmed every claimed
deletion actually happened (`backend/db/` has no more `app.db.*` backup
files, `data/archive/` no longer exists, `data/Vendor's Details.xlsx` is
genuinely absent, `data/Finance payments excel.xlsx` correctly left
alone) and the fresh `app.db` has all 10 tables at 0 rows. **Caught a real
slip the report missed**: `git status` showed `data/README.md` (a small,
genuinely git-tracked file, unrelated to any of the backup/archive
artifacts this task was scoped to) had been deleted too — contradicting
the report's own "none tracked in git, checked `git ls-files`" claim.
Not actually lost (still in git history) — restored it myself via
`git checkout HEAD -- data/README.md`, confirmed `git status` on `data/`
is clean again. The unrelated uncommitted `backend/db/models.py` diff
`git status` also shows is pre-existing work from the earlier P3
stale-comment-cleanup task, not from this reset — left alone correctly.
Both servers (`uvicorn` on 8000, Vite on 5173) confirmed left running per
the report, ready for Sarath's real upload.

**Independently verified (this session)**: confirmed `gemini_client.py`
has no visible retry code of its own because the retrying genuinely
happens inside the SDK, not skipped — consistent with the original bug
report ("`tenacity` retried per its own policy"). Simulated a real
`ServerError` directly against `map_columns()`: caught cleanly as
`AIMappingUnavailableError` with the exact message claimed. Called the new
`main.py` handler directly: returns `503` with
`{"detail": "AI column mapping is temporarily unavailable..."}`, exactly
as reported. `resolve_column_mapping()`'s one-AI-call-per-upload guarantee
confirmed unchanged — no deterministic-only fallback path was added.

**(12) Minimum Funds Required showing flat 0 for every vendor after a real
fresh upload — root cause confirmed, new Rule 1b added.** After the full
wipe (item 11), Sarath's first real upload against the sheet's own actual
first month (2026-07) hit a genuinely new edge case: Minimum Funds
Required's `as_of` reference point is always planning-month-minus-one
(`new_model_2.py::_month_minus_one()`); if Sarath picked July 2026 as the
planning month, `as_of` = June 2026 — a month that predates the sheet's
own first recorded month entirely. In `min_funds_v2.py::_compute_v2()`,
this meant `current` was `None` (nothing matches `as_of`) AND `older` was
empty (nothing is earlier than `as_of` either, since the sheet starts the
very next month) → the old Rule 1 fired and returned 0, identically for
every vendor — matching the exact uniform-zero symptom, not a per-vendor
data problem.

This wasn't covered by `docs/14`/`docs/9-open-questions.md` — a genuinely
new, previously-undiscussed edge case (planning for the sheet's own first
month, right after a fresh upload). Decision made (flagged, house-style
comment in the code, same tone as `allocator.py`'s `FALLBACK_BUCKET`):
when there's no tranche at `as_of` and no older leftover either, but real
outstanding tranches do exist (meaning every one is strictly after
`as_of`), treat the earliest outstanding tranche as the reference bill
directly — new rule tag `v2_no_history_before_as_of` — rather than
falling through to a flat, wrong 0.

**Independently verified (this session)**: read `min_funds_v2.py` in full
— the new Rule 1b block matches the report exactly. Ran
`calculate_minimum_funds_required_v2()` directly against the real DB
myself: total = 65,826,730.33 (₹6,58,26,730.33), all 421 vendors tagged
`v2_no_history_before_as_of` — matches the claimed figure exactly. Ran
`test_min_funds_v2.py` myself: 22/22 pass, matches the claimed count.

**Still open, not yet resolved**: `data/Vendor's Details.xlsx` is missing
in this sandbox too (only `Finance payments excel.xlsx`/`README.md` sit in
`data/`), despite `app.db` showing 425 real vendor rows — this is why
Claude Code's own report said it paused the full-suite/fresh-upload
comparison. Needs Sarath to confirm whether the file genuinely exists on
the real dev machine (a sync/mount gap) or the master-Excel write-back
step itself is silently failing after upload (a real rule-6 violation,
since Excel is meant to be the single source of truth) — flagged, not
diagnosed further yet.

## Finalize/Shortfall UX, AI Talking/Email toggle + ₹ fix, two Excel downloads, negative-payment fix (batch of tasks, catching this doc up)

Several tasks landed since the last update to this file and were missing
from both this tracker and `CLAUDE.md`'s narrative — recorded here as a
batch, each independently spot-checked by grep/read against the live code
(not a full test-suite re-run this round):

1. **Finalize shortfall warning — final resting place.** Went through
   three iterations: a dedicated `ShortfallModal.tsx` (3 suggested actions,
   centered) → deleted outright in favor of a standalone red banner on the
   Planning page → **now live**: the banner moved *inside* the "Finalize
   this plan?" `ConfirmModal` itself via a new `extra?: React.ReactNode`
   prop (`ConfirmModal.tsx`), so Finance sees "Short by ₹X" plus a
   suggestion line directly in the same popup where they click Finalize —
   confirmed Finalize/Cancel are still both available (not blocked), per
   Sarath's explicit "allow them to finalize but give suggestions under the
   warning." `ShortfallModal.tsx` and its test file are confirmed deleted
   (not just unused).
2. **AI Talking Points — ₹ fix + Talking/Email toggle.** `backend/ai_layer/prompt_template.py`'s
   shared guardrails now hard-require the ₹ symbol + Indian digit grouping
   and explicitly ban USD/`$`/"dollars" — fixes a real bug where Gemini was
   writing dollar amounts. `AiCompanionCard.tsx` (the consolidated AI card
   from the earlier AI Screen revamp) now has a Talking/Email segmented
   toggle backed by `POST /ai/vendor-talking-points`'s new `format` field
   (`"talking"`/`"email"`), each tab lazily fetched and cached independently.
   Banned-phrasing guardrail also added (no "our current operational plan,"
   "aging period," etc. — vendor-facing copy shouldn't explain internal
   reasoning). **Known gap, unchanged from before**: `CompanionPanel.tsx`
   (the floating-FAB entry point) still passes `variant="popup"` (small
   corner card) instead of `variant="modal"` — only `VendorDetailModal`'s
   "Ask AI" link got the bigger centered card.
3. **`GET /models/5/finalized-plan-export`** — "Download Excel" button next
   to Finalize Plan, enabled once at least one vendor has a finalized
   Budget. Downloads the **real master workbook** (never touches the
   working `EXCEL_PATH` file itself — writes to a temp copy) with two new
   appended columns: Suggested Plan Percentage, Suggested Plan Amount.
   Blank (not 0) for any vendor never finalized this cycle.
4. **`GET /models/5/min-funds-verification-export`** — "Verify Min Funds"
   button between Generate Plan and Finalize Plan, enabled once Min Funds
   has been calculated. Downloads a **brand-new** workbook (unrelated to
   `EXCEL_PATH`) with Vendor Name / Outstanding / one column per aging
   window (0-30, 31-60, ... as far as the worst-aged vendor in the file
   needs, uncapped past 120 days) / Min Funds Required — built so Finance
   can manually re-check the system's own math and decide whether to trust
   it, per Sarath's own reasoning for requesting it.
5. **Negative-payment bug, found via the Verify Min Funds export itself.**
   Finance uploaded a real sheet where 7 vendors have a negative number in
   a Payment cell some month (e.g. CAPSTECH NETWORK PVT LTD, −₹332,000 in
   June 2026) — surfaced because Outstanding and the aging-bucket columns
   disagreed by exactly that amount for those 7 vendors in the downloaded
   verification sheet. Root cause: `load_excel.py`'s running-balance walk
   (`closing = opening + payable − payment`) was turning a negative payment
   into *added debt* (subtracting a negative adds it back), while
   `aging.py`'s FIFO tranche walk was silently discarding negative payments
   entirely (`if payment <= 0: break`) — two different, both-wrong
   behaviors for the same cell. **Confirmed with Finance**: a negative
   payment cell means an over-payment recorded with the wrong sign, and
   should be treated as a real payment of that magnitude (reduces the
   balance), never as extra debt and never ignored. Fixed at the single
   source: `load_excel.py` now flips a negative payment to
   `abs(payment)` before it's stored in `MonthlyLedger.payment` (with a
   `data_quality_notes` flag on the upload banner), so every downstream
   reader (aging, min-funds, `vendors.py`) sees one already-corrected value
   with no separate fix needed — confirmed by reading the code directly
   (`load_excel.py` lines ~409-418) and confirming `aging.py`/`min_funds.py`
   only ever read `MonthlyLedger.payment`, never a raw sheet value
   independently.
6. **Aging-bucket dynamic columns** for the Verify Min Funds export — the
   export originally shipped with a single "Aging Bucket" column (oldest
   bucket label only); revised into the multi-column format described in
   item 4 above (`aging.monthly_breakdown`), confirmed against a real
   downloaded file showing columns through "421-450" for the worst-aged
   vendor in the sheet.
7. **Upload History card removed** (`MainTab.tsx`) — Sarath's call: the
   card added no value Finance used. Upload card is now full-width;
   "Revert to previous upload" moved into its top-left corner; the AI
   column-mapping warning banner (previously shown per history entry) now
   shows for the most recent upload only, above the Master Data grid.
8. **45-vendor synthetic end-to-end model validation** — a dedicated
   `test_data/` dataset (7 Must Pay, 14 Commitment, 19 Normal split P2/P3/P4,
   5 Inactive) plus `backend/validation/test_new_model_2_e2e_validation.py`
   (8 tests) built to answer Sarath's "is the round-robin allocation
   actually working correctly" doubt directly, across fully-funded/
   moderate-shortfall/severe-shortfall scenarios. One real bug found and
   fixed along the way: `leftover_topup_total` was computed correctly by
   `allocator.py` but silently stripped from the API response because
   `NewModel2GenerateResponse` didn't declare the field (same failure class
   as the earlier `ai_column_mapping_messages` bug — FastAPI's
   `response_model` drops undeclared dict keys). Re-ran this suite directly:
   8/8 pass.

**Not yet done — today's work**: Analytics tab lock ("Coming soon") and the
Configuration module revamp (Finance-friendly Audit Log, general UI/UX
pass) — prompts written (`claude_code_prompt_analytics_lock.md`,
`claude_code_prompt_config_module_docs_and_audit_log_revamp.md`), not yet
executed. Analytics tab currently still reads "Analytics is on the
roadmap — check back later," not the locked/"Coming soon" state.

**Superseded (2026-08-05)**: the Configuration module revamp above was
overtaken by a larger, more concrete task — see "Configuration tab rebuilt
into a unified Category/Priority-Tag table" below, which replaces the
Priority Buckets table entirely rather than just polishing its Audit Log.
The Analytics-lock prompt is unaffected and still not executed (Analytics
is fully built per the earlier "Analytics tab built for real" entry — the
lock prompt was superseded by that work too and should probably just be
dropped, not executed; flagging for Sarath rather than deciding here).

## Reset button, Overridden-only filter, download filenames, popup copy, and Configuration category/bucket rebuild (2026-08-05 batch, verified)

Four separate tasks, each independently verified against real code/tests
before being recorded here:

1. **`POST /models/5/reset-cycle`** — new "Reset" button on the Planning
   toolbar (right of Download Excel, disabled until a plan exists this
   cycle). Deletes the current planning month's `PlanRun`+`PlanAllocation`
   rows, reuses `_reset_vendor_cycle_state()` verbatim (same reset already
   used at month-end rollover), writes one audit entry, all in one
   transaction. Does not touch vendor master data/Excel, Configuration,
   already-finalized past months, or the confirmed planning month value.
   Real bug found and fixed along the way: `_resolve_planning_month()`
   used to always prefer "latest `PlanRun`" over the stored
   `planning.current_planning_month` config value — after Reset deletes
   the current cycle's only `PlanRun` row, that would resolve every
   endpoint back to a stale older month instead of the fresh, plan-less
   current one. Now compares the two by calendar month and takes whichever
   is later, preserving the existing "generate a later month supersedes
   stored config" behavior while fixing the reset case. Verified: 3 new
   backend tests + 3 new frontend tests all pass; independently re-ran
   `test_new_model_2_router.py` fresh — **20 passed, 1 pre-existing
   failure** (`test_minimum_funds_required_missing_planning_month_is_422`,
   unrelated to Reset, already documented elsewhere in this file as stale
   — expects 422, endpoint correctly returns 400 since its planning_month
   param became optional in an earlier task). An earlier version of this
   report claimed "22 passed" and separately over-credited a
   `PlanRunOut`/`min_funds_required` fix to this task — both corrected
   after a fresh re-run; that field was already fixed in the earlier
   "Funds Left card" task, this task's diff never touched `plan_runs.py`.
2. **"Overridden only" filter** — one more toggle in the Planning tab's
   existing Filters popover, alongside Category/Aging/Priority Tag. Shows
   only vendors whose current plan allocation has `override_amount != null`,
   combines with the other filters as AND.
3. **Download filenames now include the real download date+time**, not
   just the planning month — all 3 export endpoints fixed
   (`Min Funds Verification - {month} - {timestamp}.xlsx`,
   `Vendor Payment Plan - {month} - {timestamp}.xlsx`,
   `Vendor Analytics - {timestamp}.xlsx`), so repeat downloads in the same
   cycle no longer collide/overwrite in the browser's Downloads folder.
4. **Configuration tab rebuilt** — the old Priority Buckets table (with
   its confusing raw "Rotation Position" number) is now one unified,
   extensible Category/Priority-Tag table: Category name, Priority Tag,
   Ceiling %, Floor %, with row order itself as the cut sequence
   (drag-to-reorder, top = cut last). Must Pay/Commitment stay permanently
   fixed, non-editable, never appear as a row (rule 5). Finance can now add
   a genuinely new category (e.g. "Contractor") through a "+ Add category"
   button — it's always cuttable, gets an auto-suggested Priority Tag, and
   flows through the exact same bucket-ceiling/cutting logic as
   Normal/Inactive today. `VendorCategory` is no longer a closed 4-value
   enum for this purpose: `allocator.py`'s `_is_guaranteed()` now only
   special-cases the two fixed categories/tags (confirmed by direct code
   read — everything else falls into the cuttable pool regardless of how
   many custom categories exist), and `GET /vendors/categories` now
   genuinely reads the live category set (fixed Must Pay/Commitment +
   every live `category_name` in the priority_buckets table) instead of
   the old hardcoded enum — confirmed by direct code read of both files,
   not just trusting the execution report. Popup/toast copy across the
   Planning tab was also separately cleaned up in the same batch — "New
   Model 2"/internal model naming removed from every Finance-facing
   message (confirmed via a fresh grep of `frontend/src`, zero remaining
   hits outside code comments), and raw error strings now go through a
   `friendlyErrorMessage()` helper instead of showing exception text
   verbatim.

## Confirmed reproducible test-suite gap, 2026-08-05: 17 failures in `test_planner.py` + `test_min_funds.py` + `test_allocator.py`

Written down here so this stops getting re-litigated with a different
number by every future task — a prior investigation (the `assigned_week`
fix in `backend/api/test_new_model_2_router.py`) reported "11 new
failures" for this same command; that number was wrong/stale. Re-run fresh,
twice, right now:

```
python -m pytest backend/weekly_planning/test_planner.py backend/shared/test_min_funds.py backend/models/new_model_2/test_allocator.py -q
```

Both runs: **17 failed, 42 passed**, identical test names both times (not
flaky/order-dependent in this environment). This matches exactly what an
independent environment reported. Real file state at the time of this run:
`data/Vendor's Details.xlsx` — 453 rows / 48 columns; `backend/db/app.db` —
422 active vendors, **0 with `assigned_week` set**. Confirmed **zero
overlap** with the `assigned_week` fix — none of these 3 files import
`backend/api/test_new_model_2_router.py` or anything it touches; the shared
root cause (below) is a real, independently-discoverable dataset fact, not
a code path the two share.

**Full failing list, one-line cause each** (read every actual
traceback before classifying — not assumed):

| Test | File | Cause |
|---|---|---|
| `test_weekly_numbers_correct_for_a_real_vendor` | `test_planner.py` | Dataset-drift: `weekly_view.detail` is empty (`IndexError` on `.iloc[0]`) because **0 of 422 real vendors have `assigned_week` set** — the real sheet's header is literally `"Week"`, not the fixed default `"Assigned Week"`; this test reads the real `app.db` directly, which was never ingested through a path that resolves that renamed header. |
| `test_funding_exactly_minimum_matches_every_week` | `test_planner.py` | Same root cause — empty `weekly_summary` (`nan < 1`). |
| `test_funding_below_minimum_shows_shortfall_in_at_least_one_week` | `test_planner.py` | Same root cause — empty `weekly_summary`. |
| `test_within_week_order_orders_by_priority_tag_then_score` | `test_planner.py` | Same root cause — `KeyError: 'assigned_week'` (empty `detail` has no such column, per `planner.py`'s own comment). |
| `test_persisted_plan_allocations_match_detail` | `test_planner.py` | Same root cause — `KeyError: 'vendor_id'`. |
| `test_weekly_view_inherits_must_pay_commitment_guarantee_under_severe_shortfall` | `test_planner.py` | Same root cause — the test's own guard (`assert len(tagged) == 2, "need at least 2 real vendors with an assigned_week..."`) fires explicitly, naming the exact problem. |
| `test_week_distribution_plan_defaults_to_full_amount_in_assigned_week_when_persisted` | `test_planner.py` | Same root cause — `assert not detail.empty` fails. |
| `test_week_distribution_plan_carries_forward_from_vendor_level_sticky_value` | `test_planner.py` | Same root cause — looks up a real assigned-week vendor, finds none. |
| `test_week_distribution_plan_defaults_to_full_amount_when_not_persisted` | `test_planner.py` | Same root cause — empty `detail`. |
| `test_calculate_minimum_funds_required_from_shared_location` | `test_min_funds.py` | Classic dataset-drift, same class already documented above (memory/`demo15_dataset_quirks.md`): hardcoded `assert len(result["breakdown"]) == 83`, real count is 419. |
| `test_normal_vendor_required_amount_reflects_live_payment_immediately` | `test_min_funds.py` | Dataset-drift: picks "the first real Normal vendor with `paid_so_far_this_month==0` and `opening_balance>500k`" (currently `V00400`) and assumes a 40%-of-tranche payment stays inside one tranche (test's own docstring: "no bucket transition"). With the real sheet's current (longer-history, larger) shape that vendor's tranche structure no longer satisfies that assumption — `after_amount` comes back *larger* than expected, consistent with the carry-forward window sliding onto a next tranche the test explicitly says it's trying to avoid (`min_funds.py`'s own docstring already flags this exact transition case). |
| `test_calculate_minimum_funds_required_total_shrinks_after_a_payment` | `test_min_funds.py` | Same class as above — real vendor's current tranche shape no longer matches the "no-transition" assumption baked into the test. |
| `test_absolute_funds_gives_100_percent_not_ceiling` | `test_allocator.py` | **Not dataset-drift.** Calls `generate_plan(available_funds=...)` with no `session=` kwarg, so it opens its own session against the *real* `backend/db/app.db`; `generate_plan()`'s own unconditional `if owns_session: session.commit()` (persists any newly-seeded `PriorityBucket` rows) now trips `conftest.py`'s real-DB-write guard (`RuntimeError: TEST ISOLATION VIOLATION`). Every sibling test in the same file that passes (e.g. `test_ceiling_applies_once_funds_short_of_100_percent_for_everyone`) explicitly does `session = SessionLocal(); generate_plan(..., session=session); finally: session.rollback()` — these 4 were never updated to that pattern. Guard is working as intended; the test is the gap. Would fail against *any* real dataset, not size/shape-dependent. |
| `test_no_allocation_exceeds_outstanding_balance` | `test_allocator.py` | Same test-isolation gap as above — no `session=` passed. |
| `test_topup_never_pushes_total_allocated_past_available_funds` | `test_allocator.py` | Same test-isolation gap as above. |
| `test_topup_full_generate_plan_is_deterministic` | `test_allocator.py` | Same test-isolation gap as above. |
| `test_inactive_vendor_grouped_by_own_priority_tag_not_folded` | `test_allocator.py` | Dataset-drift-exposed test assumption, confirmed by direct probe: picks 2 real vendors (`V00400`, required≈6.35M; `V01503`, required≈2.75M), tags both P3, and asserts they land on the *identical* final allocation percentage. `_apply_leftover_topup()`'s per-vendor step size scales with each vendor's own `required_amount` (`step_amount = required_amount * PCT_STEP`) — with a large required-amount gap between the two, the smaller vendor's step fit the remaining leftover (bumped 0.90 -> 0.95) while the larger one's didn't (stayed at the bucket's base 0.90). The test's "same bucket -> same final pct" premise isn't generally guaranteed by the top-up algorithm's own design; it held for whichever 2 vendors existed at those id/balance positions when the test was written, not for the current real data. Flagged as a real design question (should top-up guarantee within-bucket parity, or is per-vendor step-feasibility intentional?) — not resolved here. |

**How to apply**: 9 of 17 are the exact same underlying fact (no real
vendor has `assigned_week` populated in `backend/db/app.db` right now) —
fixing that one ingestion/header-mapping gap for real (not per-test
work-arounds) would clear 9 failures at once. 4 of 17 are a pure
test-isolation bug (missing `session=` kwarg) with a known fix pattern
already used by every sibling test in the same file — mechanical, low-risk
fix whenever someone's in that file. 2 of 17 are vendor-specific
tranche-shape assumptions that drift as the real sheet grows — no generic
fix, just re-pick a payment amount that's provably still single-tranche
each time the real data changes, or move to a synthetic fixture. 1 of 17
(`test_inactive_vendor_grouped_by_own_priority_tag_not_folded`) surfaces a
real, worth-discussing algorithmic question in `_apply_leftover_topup()`,
not just bad test data.

## OPEN INCIDENT (2026-08-05/06) — real `app.db` found with an empty `vendors` table, unresolved, top priority for next session

**Do not start new feature work until this is checked and closed.**

While independently verifying the Configuration category/bucket rebuild
task's execution report (Vendor.category Enum→String, PriorityBucket
gaining `category_name`, a 9-step live-app verification walkthrough), a
direct query of the real `backend/db/app.db` found the `vendors` table
completely empty (0 rows). By the end of the same verification session,
`priority_buckets` was empty too (previously confirmed populated with the
real P2 95/25, P3 90/25, P4 85/25, P5 50/10 values earlier in that same
session). Both re-confirmed by direct `sqlite3`/query, not assumed.

**What's confirmed NOT lost**: `data/Vendor's Details.xlsx` — the real
source-of-truth master sheet — is intact, 453 rows / 48 columns, verified
directly with `openpyxl`. Per `docs/12-database.md`, `vendors` is a
derived, rebuildable table (ingestion reads it fresh from Excel every
time) — this is a DB-state problem, not a data-loss problem. `audit_log`
and `plan_runs` were both still populated with real historical rows at the
point `vendors` was first found empty (451 audit rows, plan_run id 22 with
`leftover_remaining≈145.20` matching the report's "restored to original"
claim) — so whatever happened hit `vendors` (and later `priority_buckets`)
specifically, not the whole DB file at once.

**Root cause not established — be honest about this, don't assert a
confident cause without re-deriving it.** Two live possibilities, not
mutually exclusive:
1. The execution report itself admitted finding and deleting "2 stray
   audit-log rows caused by running pytest concurrently against the same
   real `app.db`" during its own 9-step verification — the same risk class
   (concurrent writers against one SQLite file) could plausibly explain a
   table-level wipe too, just not caught/admitted at the time.
2. Independent verification's own test run (`pytest
   backend/configuration/test_priority_bucket_edits.py`, and an earlier
   180s-timeout multi-file run right before it) happened in the same
   window the emptiness was first observed — cannot rule out contributing,
   despite `conftest.py`'s autouse real-DB-write guard existing precisely
   to prevent this class of accident.

**First 3 things to do, in order, next session**:
1. Re-run ingestion (`POST /master-data/commit-upload` or the equivalent
   `load()` call) against the real, intact Excel file to repopulate
   `vendors`. Calling `list_buckets()` once afterward reseeds
   `priority_buckets` back to its P2-P5 defaults (95/25, 90/25, 85/25,
   50/10) — confirm those land back at the real values, not some other
   default, before trusting the Configuration tab again.
2. Re-run `backend/configuration/test_priority_bucket_edits.py` fresh
   against the restored DB. An interim run (against the broken/empty
   state) returned **5 failed, 16 passed**:
   `test_update_bucket_category_name_change_cascades_to_vendors_tagged_into_it`,
   `test_update_bucket_new_bucket_key_cascades_to_vendors_tagged_into_it`,
   `test_remove_bucket_blocked_when_a_vendor_is_tagged_into_it`,
   `test_remove_bucket_blocked_when_a_vendor_is_categorized_into_it_but_not_tagged`,
   `test_reorder_buckets_changes_generate_plans_actual_cut_order` — every
   one failed on a variant of "expected >=2 Normal vendors, found 0," which
   is consistent with the empty-vendors-table state rather than a genuine
   code defect, but this is NOT confirmed either way yet. Re-run clean
   before concluding anything.
3. Only after 1-2 are done, re-treat the execution report's overall claim
   ("backend 70 failed/398 passed, all pre-existing dataset drift,
   confirmed via git-stash comparison") as verified — it cannot be trusted
   as-is, since it was measured on top of whatever DB state preceded this
   emptiness being discovered.

This entry should be deleted/closed out only once `vendors`/
`priority_buckets` are confirmed repopulated with real data AND
`test_priority_bucket_edits.py` has a clean, explained re-run.


---

### Update (2026-08-06, new session) — DB found *fully* empty, not just vendors/priority_buckets; likely mechanism identified

Independent re-verification at the start of this session (direct sqlite3
query against the real `backend/db/app.db`) found **all four** tables
checked at 0 rows: `vendors`, `priority_buckets`, `audit_log`, and
`plan_runs`. The prior entry above only recorded `vendors`/`priority_buckets`
as empty and explicitly noted `audit_log` (451 rows) and `plan_runs`
(id 22, `leftover_remaining≈145.20`) as still-populated evidence that the
wipe was partial — that is no longer the case; the whole DB is now empty.

**Consequence**: `audit_log`/`plan_runs` history has no backup anywhere in
the repo (unlike `demo.db`, which has 30+ timestamped backups — `app.db`
has none, is gitignored, and was never git-tracked), and no WAL/journal
file exists either. That history is very likely permanently lost. Vendor
master data is NOT at risk — `data/Vendor's Details.xlsx` re-verified
intact (453 rows / 48 cols) independently of the prior session's check.

**Likely mechanism** [Likely, not confirmed — no execution log exists to
prove this ran]: an untracked `reset_realdb.py` sits in the repo root.
Its docstring: "Wipe the real app.db (and any app.db.* backup/archive
files) and recreate a fresh, empty database. No copies kept." Its
behavior (delete `app.db*`, reinit, every table back to 0 rows) exactly
matches the observed state. `app.db`'s mtime (2026-08-06 04:37:57 UTC) is
~8 minutes before the prior session's handoff docs were finalized
(04:45 UTC) — inside the same verification session that first noticed the
emptiness. Checked `backend/conftest.py`'s autouse real-DB-write guard
(added 2026-07-31 after this same class of accident happened three times
before): it intercepts `session.commit()` and `os.replace()` against the
real DB/Excel, but does **not** intercept `os.remove()` — the exact call
`reset_realdb.py` uses. So if it ran against the real DB (its default
`DB_PATH` is `app.db` unless `PAYMENTS_DB_PATH` is overridden), the
guardrail would not have caught it. Recommend either deleting
`reset_realdb.py` or hard-coding a path guard into it so it can only ever
target `demo.db`.

**Decision (Sarath, 2026-08-06)**: checked for any local backup of the old
`app.db` (OneDrive/File History) himself — accepted `audit_log`/
`plan_runs` history as unrecoverable, proceeding by uploading a fresh copy
of the real Excel through the app's normal upload/replace flow to
repopulate `vendors` (and, via the Configuration tab / `list_buckets()`,
`priority_buckets` back to its real defaults: P2 95/25, P3 90/25,
P4 85/25, P5 50/10). Next session/step: once that upload is done, verify
row counts and bucket values landed correctly, then re-run
`backend/configuration/test_priority_bucket_edits.py` fresh (step 2 of the
original 3-step plan) before closing this incident out.
