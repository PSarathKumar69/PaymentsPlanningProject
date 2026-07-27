# 17 — Frontend hardcoded-values audit

Scope: every file in `frontend/src/components/` and `frontend/src/api/`
(not just the ones a prior sampling pass flagged — all of them read fresh for
this pass). CLAUDE.md rule 7 applied specifically to the React app.

Classification: **(a)** genuinely fine, fixed structural constant. **(b)**
already self-documented, deliberate placeholder/default. **(c)** actual gap —
should eventually come from the backend/Configuration.

## Findings

| Item | Where | Class | Reasoning |
|---|---|---|---|
| `VendorCategory` closed list (`must_pay`/`commitment`/`normal`/`inactive`) | `constants/enums.ts` | (b) | Confirmed backend-side: `backend/db/models.py`'s `Vendor.category` is a Python `Enum(VendorCategory)` column, `backend/api/routers/configuration.py` has no category CRUD (only `priority-buckets` CRUD). The frontend comment's claim still holds. Already flagged in-code and in the report; a real gap in the sense that categories can't be added/removed without a code change, but that's an accepted, documented limit — not a silent hardcode. |
| `AGING_BUCKET_OPTIONS` (`0-30/31-60/61-90/91-120/120+`) | `constants/enums.ts` | (a) | Confirmed backend still hardcodes the identical list as `AGING_BUCKETS` in `backend/shared/aging.py` (grepped — still a plain Python list, not config/DB-driven). Frontend mirrors a fixed backend constant; if the backend ever became configurable this would need to follow, but today it's a correct 1:1 mirror, not an independent hardcode. |
| `AUTO_PRIORITY_TAG` (`must_pay -> P0`, `commitment -> P1`), `FIXED_TAG_FOR_CATEGORY`, `'P5'` literal for Inactive, `DEFAULT_NORMAL_TAG = 'P2'`, `NON_NORMAL_TAGS` | `constants/enums.ts`, `PlanningView.tsx` | (b) | Checked `backend/db/models.py`: `Vendor.priority_tag` is a free `String`, no DB-level link from category `inactive` to a specific `bucket_key` the way Normal vendors' tags come from `GET /config/priority-buckets`. P0/P1/P5 are structurally fixed-by-category (Must Pay/Commitment/Inactive are never Finance-picked tags), so hardcoding those three specific literals is the correct mirror of a real backend invariant, not a laziness shortcut — same reasoning the existing comments already give, confirmed still accurate. |
| `PLACEHOLDER_CURRENT_USER` (`SK`/`Sarath Kumar`/`Finance Admin`) | `constants/currentUser.ts` | (b) | Grepped all usages: only `App.tsx` (logout toast text) and `Sidebar.tsx` (profile card chrome). Zero uses in any authorization/permission-gating decision. Confirmed still purely cosmetic, matches the deferred-auth note in docs/9-open-questions.md. |
| Role-gating comment ("mocked client-side placeholder") | `ConfigurationTab.tsx` | (b) | Same deferred-auth item as above — no real RBAC exists anywhere yet, consistent with docs/9/docs/15. |
| `useState(5)` initial `weeksInMonth` | `PlanningView.tsx` | **(c) — minor, flag** | Real (if narrow) race: `planningMonth` is initialized synchronously (`currentMonthValue()`), so the `getWeeksInMonth()` effect fires on the same mount as vendor loading, but it's still a separate async network call — until it resolves, the Assigned Week `<select>` (line ~1087) renders with the default 5 options. If that request is slow relative to the vendor-list load (or a user picks a new month and the table re-renders before the new count lands), a 4-week month could briefly offer a selectable "W5". Same race recurs on every `planningMonth` change, not just initial mount. Not fixing here (audit only) — worth a follow-up: disable/hide the field, or gate the table's first paint, until the real count lands. |
| `minFundsSentence()` 50% wording | `VendorDetailModal.tsx` | (a) | Confirmed the function only selects among pre-written sentence templates keyed off `detail.rule` (`v2_oldest_and_current`/`v2_oldest_and_second`/`v2_oldest_only`/commitment) — a string the backend already computed and returned. The "50%" text is narration of a decision the backend made, never an independent frontend threshold check. Matches docs/14's deterministic-template spec exactly (comment already says so). |
| Bucket ceiling/floor percentages (95/90/85/50/25/10) | grepped across `frontend/src/components` | (a) | Confirmed zero appearances outside `ConfigurationTab.tsx` (fetches/edits `ceiling_pct`/`floor_pct` live via `GET/POST/PUT /config/priority-buckets`) and the `minFundsSentence()` narration above (not a frontend-decided threshold). No calculation logic anywhere else references these numbers. |
| `formatMoney`/`formatPct`/`formatMonthShortYear`/`formatMonthLong` | `utils/format.ts` | (a) | Pure display formatting (`Math.round` + `toLocaleString` for money, percentage string formatting, UTC-safe month labels). No rounding/epsilon logic that could diverge from the backend's `MONEY_EPSILON` (`backend/shared/constants.py`) — that constant governs backend equality/reconciliation checks, not display rounding; the two don't overlap or need to agree. |
| Mock data / `// TEMP MOCK` comments | whole `frontend/src` | (a) — clean | `frontend/src/mocks/` does not exist (glob returned nothing). Grepped `mock` (case-insensitive) across all of `frontend/src`: only two hits, both comments describing a *documented, already-scoped* placeholder — `api/client.ts`'s header comment (referencing docs/15's "swap mock for real api call" history) and `ConfigurationTab.tsx`'s role-gating comment (covered above). No literal mock data or dead mock imports remain. |
| Excel column mapping, weeks-in-month, priority-bucket tags | `api/masterData.ts`, `api/calendar.ts`, `api/configuration.ts` | (a) | All three explicitly fetch from the backend already (own in-code comments cite CLAUDE.md rule 7 directly) — correctly NOT hardcoded. Verified each has a real, called backend endpoint (see docs/16) and no local fallback list shadowing it. |

## Not found

No additional status/category/tag/threshold string typed directly into a
component instead of being imported from `constants/enums.ts` or fetched from
the backend, beyond the items above — checked every `.tsx` file in
`frontend/src/components/` (`ShortfallModal.tsx`, `CompanionPanel.tsx`,
`NotificationToast.tsx`, `ConfigurationTab.tsx`, `Sidebar.tsx`, `MainTab.tsx`,
`ConfirmModal.tsx`, `SuccessCheck.tsx`, `VendorDetailModal.tsx`,
`PlanningView.tsx`) and every `frontend/src/api/*.ts` file.

## Summary verdict

Nothing found that silently duplicates backend decision logic. One real
(minor) UI-only gap: the `weeksInMonth` default-5 race. Everything else
flagged is either a confirmed-accurate mirror of a genuine backend constant/
invariant, or an already-documented, intentionally deferred placeholder.
