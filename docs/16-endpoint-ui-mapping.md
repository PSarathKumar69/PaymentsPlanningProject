# 16 — Backend endpoint <-> React UI mapping

Scope: `frontend/` (Vite React app) only. `test_ui.html` not covered — separate,
out of scope.

Re-derived fresh via `grep -rn "@router\.(get|post|patch|put|delete)" backend/api/routers`
against the live routers, then cross-checked every `frontend/src/api/*.ts` export
against every `frontend/src/components/*.tsx` import. 34 routes total, all
accounted for below.

## Full mapping table

| Method | Path | Router file | React API wrapper | Called from (component) | Note |
|---|---|---|---|---|---|
| POST | `/ingestion/load` | `ingestion.py` | — none — | — | Test-only. `POST /master-data/commit-upload` is the real UI upload path and drives the same `backend/ingestion/load_excel.py` machinery under the hood — confirmed by reading `master_data.py`'s commit-upload handler. Intentional, not a gap. |
| POST | `/master-data/commit-upload` | `master_data.py` | `masterData.ts::commitUpload` (via `api.upload()`, not `api.post()`) | `MainTab.tsx` | Single-button upload, no preview step. |
| POST | `/master-data/revert` | `master_data.py` | `masterData.ts::revertUpload` | `MainTab.tsx` | Restores the one backup slot. |
| GET | `/master-data/grid` | `master_data.py` | `masterData.ts::getMasterGrid` | `MasterDataGrid.tsx` (rendered inside `MainTab.tsx`) | **Closed since this doc was first written**: was a real gap (no React caller at all); `MasterDataGrid.tsx` now wires it, reloading on mount and after every upload/revert. |
| GET | `/master-data/extra-fields` | `master_data.py` | `masterData.ts::getExtraFields` | **— none —** | Still unused — `MasterDataGrid.tsx` reads extra-field values/widgets off `GET /master-data/grid`'s own `extra_field_widgets` + per-vendor `values`, not this separate endpoint. Not a gap: nothing needs the standalone list this returns. |
| PATCH | `/master-data/extra-fields/{vendor_id}` | `master_data.py` | `masterData.ts::patchExtraField` | `MasterDataGrid.tsx` | Closed alongside the grid endpoint above — every `"extra"`-kind cell edit calls this. |
| POST | `/rollover` | `rollover.py` | **— none —** | — | **No wrapper file, no caller.** Month-end rollover has zero UI entry point in the React app. Flagged prominently below — real, consequential gap. |
| DELETE | `/config/priority-buckets/{bucket_key}` | `configuration.py` | `configuration.ts::removePriorityBucket` | `ConfigurationTab.tsx` | |
| DELETE | `/plan-runs/{plan_run_id}` | `plan_runs.py` | `planRuns.ts::deletePlanRun` | `PlanningView.tsx` | |
| GET | `/audit-log` | `audit_log.py` | **— none —** | — | **No wrapper file, no caller.** No viewer anywhere for the audit trail CLAUDE.md rule 6 requires. Flagged prominently below. |
| GET | `/calendar/weeks-in-month` | `calendar.py` | `calendar.ts::getWeeksInMonth` | `PlanningView.tsx` | |
| GET | `/config` | `configuration.py` | **— none —** | — | Generic system-config key/value table (e.g. `backend/shared/scoring.py`'s weights) has no Configuration-tab UI; only `/config/priority-buckets` does. Flag as a gap, may be legitimately deferred until those weights need to be Finance-editable. |
| GET | `/config/priority-buckets` | `configuration.py` | `configuration.ts::getPriorityBuckets` | `ConfigurationTab.tsx`, `PlanningView.tsx` | |
| GET | `/models/5/minimum-funds-required` | `new_model_2.py` | `newModel2.ts::getMinimumFundsRequired` | `PlanningView.tsx` | |
| GET | `/models/5/plan-runs` | `new_model_2.py` | `newModel2.ts::getPlanRuns` | `PlanningView.tsx` | |
| GET | `/models/5/vendor-min-funds-required` | `new_model_2.py` | `newModel2.ts::getAllVendorMinFundsRequired` | `PlanningView.tsx` | Bulk, Planning table's Total Min Funds column. |
| GET | `/models/5/vendors/{vendor_id}/min-funds-required` | `new_model_2.py` | `newModel2.ts::getVendorMinFundsRequired` | `PlanningView.tsx`, `VendorDetailModal.tsx` | |
| GET | `/vendors` | `vendors.py` | `vendors.ts::listVendors` | `PlanningView.tsx` | |
| GET | `/vendors/aging` | `vendors.py` | `vendors.ts::getAllVendorsAging` | `PlanningView.tsx` | Bulk form, replaced ~83 individual calls. |
| GET | `/vendors/payment-tracking` | `vendors.py` | `vendors.ts::getVendorPaymentTracking` | `PlanningView.tsx` | |
| GET | `/vendors/{vendor_id}` | `vendors.py` | `vendors.ts::getVendor` | **— none —** | Defined, unused — `PlanningView.tsx` gets vendor rows from bulk `listVendors()`; `VendorDetailModal.tsx` receives its vendor as a prop from the already-loaded list rather than re-fetching by id. Not a gap — just an unused convenience wrapper. |
| GET | `/vendors/{vendor_id}/aging` | `vendors.py` | `vendors.ts::getVendorAging` | `PlanningView.tsx`, `VendorDetailModal.tsx` | |
| GET | `/vendors/{vendor_id}/payments` | `vendors.py` | **— none —** | — | **No wrapper file, no caller.** `VendorDetailModal.tsx` only lets Finance log a *new* payment; it never displays past payments for that vendor. Flag as a gap. |
| PATCH | `/plan-allocations/{plan_allocation_id}/override` | `plan_allocations.py` | `planAllocations.ts::patchOverride` | `PlanningView.tsx` | |
| PATCH | `/plan-allocations/{plan_allocation_id}/week-distribution` | `plan_allocations.py` | `planAllocations.ts::patchWeekDistribution` | `PlanningView.tsx` | |
| PATCH | `/vendors/{vendor_id}` | `vendors.py` | `vendors.ts::patchVendor` | `PlanningView.tsx` | Category/priority-tag/assigned-week edits. |
| POST | `/ai/talking-scripts` | `ai_layer.py` | `aiCompanion.ts::postTalkingScripts` | `VendorDetailModal.tsx` | Gated on latest allocation status `'zero'`. |
| POST | `/ai/vendor-talking-points` | `ai_layer.py` | `aiCompanion.ts::postVendorTalkingPoints` | `CompanionPanel.tsx` | |
| POST | `/config/priority-buckets` | `configuration.py` | `configuration.ts::addPriorityBucket` | `ConfigurationTab.tsx` | |
| POST | `/models/5/finalize` | `new_model_2.py` | `newModel2.ts::finalizeNewModel2` | `PlanningView.tsx` | |
| POST | `/models/5/generate-plan-and-weekly-view` | `new_model_2.py` | `newModel2.ts::generatePlanAndWeeklyView` | `PlanningView.tsx` | Same endpoint for first-generate and every regenerate (docs/14). |
| POST | `/payments` | `payments.py` | `payments.ts::postPayment` | `VendorDetailModal.tsx` | |
| POST | `/vendors/finalize-plan` | `vendors.py` | `vendors.ts::finalizePlan` | **— none —** | Exported (default `model=1`) but never imported/called anywhere in `frontend/src`. New Model 2's own tab uses `finalizeNewModel2()` (`POST /models/5/finalize`) instead, which snapshots with `model=5` server-side through this same underlying code path. This generic wrapper appears to be dead code from the React side — see ambiguity note below. |
| POST | `/weekly-view` | `weekly_planning.py` | **— none —** | — | Router's own docstring: a standalone, flexibility/testing wrapper around `build_weekly_view()`, independent of the per-model convenience endpoints, with direct pytest coverage (`backend/api/test_api.py`) but no UI caller by design. Intentionally UI-unmapped — do not delete, do not wire up. |
| PUT | `/config/priority-buckets/{bucket_key}` | `configuration.py` | `configuration.ts::updatePriorityBucket` | `ConfigurationTab.tsx` | |

34/34 endpoints from the live-router re-grep accounted for above — matches the
prior scoping pass's count.

## Endpoints with no React caller — summary

| Endpoint | Verdict | Why |
|---|---|---|
| `POST /ingestion/load` | Intentional | Test-only; `commit-upload` is the real path. |
| **`POST /rollover`** | **Real gap — significant** | Recurring month-end operational workflow, zero UI entry point anywhere. |
| **`GET /audit-log`** | **Real gap — significant** | CLAUDE.md rule 6 requires every override logged; nothing in the UI lets Finance view that log. |
| `GET /config` | Real gap, likely deferrable | No Configuration-tab UI for the generic system-config table (e.g. scoring weights); only bucket config is wired. |
| `GET /vendors/{vendor_id}/payments` | Real gap | Vendor detail modal can log a payment but never shows past ones. |
| `GET /master-data/grid`, `PATCH /master-data/extra-fields/{vendor_id}` | **Closed** | Built into the React app via `MasterDataGrid.tsx` inside `MainTab.tsx` — see the mapping table above. |
| `GET /master-data/extra-fields` | Not a gap | Superseded by `GET /master-data/grid`'s own `extra_field_widgets` field; nothing needs this separate list endpoint. |
| `POST /weekly-view` | Intentional | Own docstring says API-level/testing tool, independent of the UI-facing per-model endpoints. |
| `GET /vendors/{vendor_id}` | Not a gap | Unused convenience wrapper; callers already have the vendor object from bulk fetch/props. |
| `POST /vendors/finalize-plan` | Ambiguous, flagged below | Exported but unreferenced — likely dead from the React side. |

## Ambiguity flagged

`vendors.ts::finalizePlan()` (wraps `POST /vendors/finalize-plan`, `model=1`
default) is exported but has zero callers anywhere in `frontend/src`. New Model
2's own `finalizeNewModel2()` covers the only finalize flow the React UI
actually uses. This mirrors the same dead-default finding already flagged
against the backend endpoint itself in the previous cleanup pass — not
re-fixing here (out of scope for a research task), just re-confirming it's
still true on the frontend side too.
