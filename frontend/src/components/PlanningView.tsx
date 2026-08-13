import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { PiggyBank, Search, SlidersHorizontal, CheckCircle2, RefreshCw, RotateCcw, Users, AlertCircle, Handshake, Layers, Trash2, Filter, Download, UserX } from 'lucide-react';
import { listVendors, getVendorAging, getAllVendorsAging, getVendorCategories, getVendorPaymentTracking, patchVendor } from '../api/vendors';
import { getPlanRuns, getMinimumFundsRequired, getAllVendorMinFundsRequired, generatePlanAndWeeklyView, finalizeNewModel2, resetNewModel2Cycle, getCurrentPlanningMonth } from '../api/newModel2';
import { downloadFinalizedPlanExport, downloadMinFundsVerificationExport } from '../api/planExport';
import { patchOverride, patchWeekDistribution } from '../api/planAllocations';
import { deletePlanRun } from '../api/planRuns';
import { getPriorityBuckets } from '../api/configuration';
import { getWeeksInMonth } from '../api/calendar';
import { ApiError, friendlyErrorMessage } from '../api/client';
import {
  PlanAllocationRow,
  PlanRunAllocation,
  PlanRunHistory,
  PriorityBucket,
  Vendor,
  VendorAging,
  VendorCategoryOption,
  VendorPaymentTracking,
} from '../types';
import {
  AGING_BUCKET_BADGE_CLASS,
  AGING_BUCKET_OPTIONS,
  AUTO_PRIORITY_TAG,
  CATEGORY_LABEL,
  CATEGORY_OPTIONS,
  VENDOR_CATEGORY,
  VendorCategory,
  categoryBadgeClass,
  categoryLabel,
} from '../constants/enums';
import { formatMoney, formatPct } from '../utils/format';
import { VendorDetailModal } from './VendorDetailModal';
import { CompanionPanel } from './CompanionPanel';
import { ConfirmModal } from './ConfirmModal';
import { ToastVariant } from './NotificationToast';

interface PlanningViewProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
  // Bump this from the parent after a successful upload/rollover to force a
  // reload — same refreshSignal convention as MasterDataGrid.tsx. Without
  // this, PlanningView (mounted once at app start, kept alive but hidden via
  // CSS when switching tabs — see App.tsx) never re-fetches, so a fresh
  // upload only shows up after a full page reload.
  refreshSignal?: number;
}

const currentMonthValue = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

const moneyDigits = (s: string) => s.replace(/[^0-9]/g, '');
const moneyText = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;
// Same "under a rupee is rounding noise, not a real mismatch" convention as
// backend/shared/constants.py's MONEY_EPSILON (1.0) — kept in sync manually
// since this is plain client-side arithmetic, not a value read from the API.
const MONEY_EPSILON = 1;

// Override % and Override Amt are two views of the same one number — typing
// into either keeps the other in sync live (matches test_ui.html's
// onOverridePctInput()/onOverrideAmtInput()), and only one PATCH fires on
// blur, using whichever field was edited last. `key`'d by the caller so a
// server-side save/clamp/regenerate remounts this with fresh initial text
// (same R1 fix as the rest of this file), rather than needing its own
// prop-sync effect.
function OverrideCells({ suggestedAmount, denom, currentOverride, tdClass, onSave }: {
  suggestedAmount: number;
  denom: number | null;
  currentOverride: number | null;
  tdClass: string;
  onSave: (amount: number | null) => void;
}) {
  const [pctText, setPctText] = useState(
    currentOverride != null && denom ? formatPct(currentOverride, denom).replace('%', '') : ''
  );
  const [amtText, setAmtText] = useState(currentOverride != null ? moneyText(currentOverride) : '');

  const currentAmount = () => {
    const digits = moneyDigits(amtText);
    return digits === '' ? null : Number(digits);
  };

  const handlePctInput = (raw: string) => {
    setPctText(raw);
    const pct = parseFloat(raw);
    if (raw.trim() === '') {
      setAmtText('');
    } else if (!Number.isNaN(pct) && denom) {
      setAmtText(moneyText((pct / 100) * denom));
    }
  };

  const handleAmtInput = (raw: string) => {
    const digits = moneyDigits(raw);
    setAmtText(digits === '' ? '' : moneyText(Number(digits)));
    if (digits === '') {
      setPctText('');
    } else if (denom) {
      setPctText(formatPct(Number(digits), denom).replace('%', ''));
    }
  };

  const handleBlur = () => onSave(currentAmount());
  const suggestedPct = denom ? formatPct(suggestedAmount, denom).replace('%', '') : '';

  return (
    <>
      <td className={`${tdClass} text-right`} onClick={(e) => e.stopPropagation()}>
        <input
          type="text"
          inputMode="decimal"
          placeholder={suggestedPct}
          value={pctText}
          onChange={(e) => handlePctInput(e.target.value)}
          onBlur={handleBlur}
          className="w-16 text-right bg-transparent border border-gray-200 rounded px-1.5 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
        />
      </td>
      <td className={`${tdClass} text-right`} onClick={(e) => e.stopPropagation()}>
        <input
          type="text"
          inputMode="numeric"
          placeholder={moneyText(suggestedAmount)}
          value={amtText}
          onChange={(e) => handleAmtInput(e.target.value)}
          onBlur={handleBlur}
          className="w-24 text-right bg-transparent border border-gray-200 rounded px-1.5 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
        />
      </td>
    </>
  );
}

// Small per-column text filter (item 2, this task) — funnel icon in a
// header cell, click opens a single text input, typing filters that
// column. State-driven click-to-open/click-outside-to-close (not the
// CSS-only group-hover pattern the main Filters popover used to use before
// this same task's item 0 fix) — a click inside the popover never races
// against losing hover mid-click.
function ColumnFilterIcon({ column, label, active, isOpen, onToggle, value, onChange }: {
  column: string;
  label: string;
  active: boolean;
  isOpen: boolean;
  onToggle: () => void;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <span data-column-filter className="relative inline-flex ml-1 normal-case font-normal" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        title={`Filter ${label}`}
        onClick={onToggle}
        className={`p-0.5 rounded cursor-pointer ${active ? 'text-[#107c41]' : 'text-gray-400 hover:text-gray-600'}`}
      >
        <Filter className="w-3 h-3" fill={active ? 'currentColor' : 'none'} />
      </button>
      {isOpen && (
        <div className="absolute z-50 top-full left-0 mt-1 bg-white border border-gray-200/90 rounded-lg shadow-2xs p-2 w-36">
          <input
            autoFocus
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={`Filter ${label}…`}
            className="w-full text-[11px] text-gray-700 border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
          />
        </div>
      )}
    </span>
  );
}

// Must Pay/Commitment's own auto-derived tags (P0/P1) — never Finance-picked.
// Single source: constants/enums.ts's AUTO_PRIORITY_TAG, not a fresh literal
// here (CLAUDE.md rule 7 — status/tag vocabulary lives in one shared module).
const AUTO_PRIORITY_TAGS = Object.values(AUTO_PRIORITY_TAG);

// Category <-> priority-tag correspondence (Finance's own rule): Must Pay
// is always P0, Commitment is always P1 — never manually reassignable
// independent of category. Every OTHER category (Normal, Inactive, or any
// custom category Configuration adds) has real choice among its OWN
// bucket rows (Normal: P2/P3/P4; Inactive: P5 today, just one row; a
// custom category: whatever row(s) Configuration gave it) — resolved live
// from `priorityBuckets` (Configuration's own bucket table, now carrying a
// `category_name` per row — Configuration-tab-rebuild task) rather than a
// hardcoded P5<->Inactive link, so a brand-new category's own tag resolves
// correctly with zero code changes here on the next add.
const FIXED_TAG_FOR_CATEGORY: Partial<Record<string, string>> = {
  [VENDOR_CATEGORY.MUST_PAY]: AUTO_PRIORITY_TAG.must_pay,
  [VENDOR_CATEGORY.COMMITMENT]: AUTO_PRIORITY_TAG.commitment,
};
function impliedCategoryForTag(tag: string, priorityBuckets: PriorityBucket[]): string {
  if (tag === AUTO_PRIORITY_TAG.must_pay) return VENDOR_CATEGORY.MUST_PAY;
  if (tag === AUTO_PRIORITY_TAG.commitment) return VENDOR_CATEGORY.COMMITMENT;
  return priorityBuckets.find((b) => b.bucket_key === tag)?.category_name ?? VENDOR_CATEGORY.NORMAL;
}

// This category's own bucket rows, in rotation (cut) order — the live
// option set for its V-Priority dropdown (Normal: P2/P3/P4; Inactive: P5
// only; a custom category: whatever Configuration gave it).
function tagsForCategory(category: string, priorityBuckets: PriorityBucket[]): string[] {
  return [...priorityBuckets]
    .filter((b) => b.category_name === category)
    .sort((a, b) => a.rotation_position - b.rotation_position)
    .map((b) => b.bucket_key);
}

// Single source for "what tag does this vendor's row actually SHOW" — the
// V-Priority cell defaults a vendor with no priority_tag yet (or a stray
// tag left over from a previous category) to its own category's
// highest-priority bucket, so anything comparing against a vendor's tag
// (the priority-tag filter, in particular) must resolve the same default
// or it'll silently disagree with what's on screen (bug fix, prior task:
// filtering by "P2" used to hide a vendor whose row visibly read P2,
// because the filter compared the raw un-defaulted null instead).
function displayedPriorityTag(
  vendor: { category: string; priority_tag: string | null },
  priorityBuckets: PriorityBucket[]
): string {
  const fixed = FIXED_TAG_FOR_CATEGORY[vendor.category];
  if (fixed) return fixed;
  const ownTags = tagsForCategory(vendor.category, priorityBuckets);
  if (vendor.priority_tag && ownTags.includes(vendor.priority_tag)) return vendor.priority_tag;
  return ownTags[0] ?? priorityBuckets[0]?.bucket_key ?? 'P2';
}

export const PlanningView: React.FC<PlanningViewProps> = ({ onNotify, refreshSignal }) => {
  // ---- loaded data --------------------------------------------------------
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [agingByVendorId, setAgingByVendorId] = useState<Record<number, VendorAging>>({});
  const [paymentTracking, setPaymentTracking] = useState<VendorPaymentTracking[]>([]);
  const [planHistory, setPlanHistory] = useState<PlanRunHistory | null>(null);
  const [nm2MinFundsByVendorId, setNm2MinFundsByVendorId] = useState<Record<number, number>>({});
  const [priorityBuckets, setPriorityBuckets] = useState<PriorityBucket[]>([]);
  // Category value/label source (P2 demo-polish task, CLAUDE.md rule 7) —
  // seeded from constants/enums.ts's static list only as a same-session
  // bootstrap default (no race risk the way weeksInMonth had: this set
  // isn't month-dependent), overwritten once GET /vendors/categories lands.
  const [categoryOptions, setCategoryOptions] = useState<VendorCategoryOption[]>(
    CATEGORY_OPTIONS.map((value) => ({ value, label: CATEGORY_LABEL[value] }))
  );
  // Rich, freshly-generated cache (this session only) — status/rule fields
  // that don't survive into the coarse plan-run history (test_ui.html's
  // planDataByModel[5]). Drives the AI-script gate and the companion's
  // vendor pool, same as upstream.
  const [richPlanByVendorId, setRichPlanByVendorId] = useState<Record<number, PlanAllocationRow>>({});
  const [loadError, setLoadError] = useState('');
  const [isLoadingAll, setIsLoadingAll] = useState(true);

  // ---- four-card cycle flow ------------------------------------------------
  const [planningMonth, setPlanningMonth] = useState(currentMonthValue());
  // Calendar-derived (CLAUDE.md rule 7) — Feb has only 4, never assume 5.
  // null = "not yet known" (docs/17's flagged race fix) — NOT a fallback of
  // 5 (could briefly offer an invalid W5 on a 4-week month) or 4 (would just
  // move the race to briefly hiding a real W5). The week `<select>` below
  // renders only the vendor's own already-assigned week (if any) while this
  // is null, and the full real range once it resolves.
  const [weeksInMonth, setWeeksInMonth] = useState<number | null>(null);
  const [fundsRequiredFigure, setFundsRequiredFigure] = useState<number | null>(null);
  const [fundsInputEnabled, setFundsInputEnabled] = useState(false);
  const [availableFunds, setAvailableFunds] = useState('');
  // Bug fix: this input re-renders every keystroke with commas/₹ inserted
  // (value={... .toLocaleString('en-IN')}) — a plain controlled input resets
  // the caret to the end on every value change whose string length shifts
  // (e.g. a comma appearing), so typing anywhere but at the very end felt
  // broken. fundsInputRef + pendingCaretDigits restore the caret after each
  // reformat by digit position (not string index), since formatting only
  // adds punctuation around the same digits, never reorders them.
  const fundsInputRef = useRef<HTMLInputElement>(null);
  const pendingCaretDigits = useRef<number | null>(null);
  const handleFundsInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const cursorPos = e.target.selectionStart ?? e.target.value.length;
    pendingCaretDigits.current = moneyDigits(e.target.value.slice(0, cursorPos)).length;
    setAvailableFunds(moneyDigits(e.target.value));
  };
  useLayoutEffect(() => {
    const el = fundsInputRef.current;
    const target = pendingCaretDigits.current;
    if (!el || target == null) return;
    pendingCaretDigits.current = null;
    const formatted = el.value;
    let digitsSeen = 0;
    let pos = formatted.length;
    for (let i = 0; i < formatted.length; i++) {
      if (digitsSeen === target) { pos = i; break; }
      if (/[0-9]/.test(formatted[i])) digitsSeen++;
    }
    el.setSelectionRange(pos, pos);
  }, [availableFunds]);
  const [fundsLeft, setFundsLeft] = useState<number | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  // Button status states (Finance's ask — this task): Generate/Regenerate
  // already had isGenerating; these three are new — Downloading… on both
  // download buttons, a full-page overlay while Finalize actually runs.
  const [isDownloadingMinFunds, setIsDownloadingMinFunds] = useState(false);
  const [isDownloadingExport, setIsDownloadingExport] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);

  // ---- search / filter -----------------------------------------------------
  // Empty set = "no filter applied for this section" (matches everything),
  // same convention across all three so they can be combined freely: OR
  // within a section (any checked category matches), AND across sections.
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilters, setCategoryFilters] = useState<Set<VendorCategory>>(new Set());
  const [agingBucketFilters, setAgingBucketFilters] = useState<Set<string>>(new Set());
  const [priorityTagFilters, setPriorityTagFilters] = useState<Set<string>>(new Set());
  // Single on/off toggle, not a multi-select set like the three above —
  // "false" matches everything (no filter applied).
  const [overriddenOnly, setOverriddenOnly] = useState(false);
  // Per-column filters (item 2, this task) — one free-text value per column
  // id, scoped to the table's static identity/scalar columns (Vendor, ERP
  // code, Assigned Week, Outstanding). Category/Aging bucket/Priority tag
  // deliberately do NOT get their own column icon too — they already have
  // the dedicated Filters popover above; a second filter UI for the same
  // three fields would just be a confusing duplicate. The dynamic Plan-N
  // (Suggested/Override) and week-distribution column groups don't get one
  // either — those are regenerated fresh per plan run, not stable
  // filterable vendor attributes, and their count varies at runtime.
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
  const [openColumnFilter, setOpenColumnFilter] = useState<string | null>(null);
  const toggleInSet = <T,>(setter: React.Dispatch<React.SetStateAction<Set<T>>>, value: T) => {
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value); else next.add(value);
      return next;
    });
  };
  const categoryValues = categoryOptions.map((o) => o.value) as VendorCategory[];
  const categoryLabelByValue: Record<string, string> = Object.fromEntries(categoryOptions.map((o) => [o.value, o.label]));
  const activeFilterCount = categoryFilters.size + agingBucketFilters.size + priorityTagFilters.size + (overriddenOnly ? 1 : 0);
  const clearAllFilters = () => {
    setCategoryFilters(new Set());
    setAgingBucketFilters(new Set());
    setPriorityTagFilters(new Set());
    setOverriddenOnly(false);
  };

  // ---- reconsider (per-cycle UI-only decision, never carried forward) ------
  const [reconsiderYes, setReconsiderYes] = useState<Record<number, boolean>>({});

  // ---- vendor detail modal ---------------------------------------------------
  const [selectedVendorId, setSelectedVendorId] = useState<number | null>(null);
  const [showPaymentInModal, setShowPaymentInModal] = useState(true);

  // ---- Plan-N column groups (one per plan_run, oldest first) ---------------
  // Which plan_run_ids are currently collapsed to a sliver column — default:
  // only the latest starts expanded. Recomputed whenever the actual SET of
  // plan_runs changes (a new one generated, or one deleted) — NOT on every
  // planHistory refetch, since overrides/distribution edits also refetch
  // plan history and shouldn't silently re-collapse whatever Finance had
  // manually expanded.
  const [collapsedPlanIds, setCollapsedPlanIds] = useState<Set<number>>(new Set());
  const togglePlanCollapse = (planRunId: number) => {
    setCollapsedPlanIds((prev) => {
      const next = new Set(prev);
      if (next.has(planRunId)) next.delete(planRunId); else next.add(planRunId);
      return next;
    });
  };

  const hasGeneratedThisCycle = !!planHistory && planHistory.plan_runs.length > 0;
  const latestPlanRun = planHistory && planHistory.plan_runs.length ? planHistory.plan_runs[planHistory.plan_runs.length - 1] : null;

  const latestAllocationByVendorId = useMemo(() => {
    const map = new Map<number, PlanRunAllocation>();
    latestPlanRun?.allocations.forEach((a) => map.set(a.vendor_id, a));
    return map;
  }, [latestPlanRun]);

  // Signed Available-Funds-vs-committed preview for the Finalize modal (item 1,
  // this task) — mirrors the backend Finalize check's own leftover
  // computation (new_model_2.py's post_new_model_2_finalize, same
  // pinned-vs-cuttable split allocator.py's leftover_remaining uses),
  // computed client-side from state already on hand rather than calling
  // POST /models/5/finalize speculatively (that endpoint COMMITS the Budget
  // snapshot, so calling it just to preview a number would be a real side
  // effect). Positive = surplus, negative = shortfall — unlike the
  // backend's own `over_by` (which floors at 0 and throws the surplus case
  // away), this keeps the sign so the modal can show either a red or a
  // green card.
  //
  // Bug fix (this task): this used to sum EVERY vendor flat against
  // availableFunds, which double-counts the guaranteed (Must Pay/
  // Commitment) tier's own inherent shortfall as if Finance could fix it by
  // cutting Normal/Inactive vendors — inflated the shown "Short by" figure
  // far past the real cuttable shortfall the Funds Left card already shows
  // correctly. Pinned-tier membership mirrors allocator.py's own
  // is_pinned_vendor() check: category matches a pinned bucket's
  // category_name, or priority_tag matches a pinned bucket_key directly —
  // `deletable === false` is exactly how the backend already flags a
  // pinned (Must Pay/Commitment) PriorityBucket row for the frontend
  // (CLAUDE.md rule 5).
  const financeSignedDiff = useMemo(() => {
    const availableFundsNum = Number(moneyDigits(availableFunds)) || 0;
    const pinnedBuckets = priorityBuckets.filter((b) => !b.deletable);
    const pinnedBucketKeys = new Set(pinnedBuckets.map((b) => b.bucket_key));
    const pinnedCategories = new Set(pinnedBuckets.map((b) => b.category_name));
    const vendorById = new Map(vendors.map((v) => [v.id, v]));

    let pinnedCommitted = 0;
    let otherCommitted = 0;
    (latestPlanRun?.allocations || []).forEach((a) => {
      const amount = a.override_amount ?? a.allocated_amount;
      const vendor = vendorById.get(a.vendor_id);
      const isPinned = !!vendor && (pinnedCategories.has(vendor.category) || pinnedBucketKeys.has(vendor.priority_tag ?? ''));
      if (isPinned) pinnedCommitted += amount; else otherCommitted += amount;
    });

    const leftover = Math.max(availableFundsNum - pinnedCommitted, 0) - otherCommitted;
    return leftover;
  }, [availableFunds, latestPlanRun, priorityBuckets, vendors]);

  // Union of week keys actually present across every vendor's stored
  // distribution — never a hardcoded W1-W5 (CLAUDE.md rule 7, week count is
  // calendar-derived).
  const weekColumns = useMemo(() => {
    const weeks = new Set<number>();
    Object.values(planHistory?.vendor_week_distribution_plans || {}).forEach((plan) => {
      Object.keys(plan || {}).forEach((w) => weeks.add(Number(w)));
    });
    // A week a vendor is actually assigned to (Vendor.assigned_week) must show
    // a column even before Finance has typed any distribution figure into it.
    vendors.forEach((v) => { if (v.assigned_week) weeks.add(v.assigned_week); });
    // Bug fix (this task): a week with a real logged payment must stay
    // visible even if no vendor's CURRENT distribution/assigned_week
    // references it anymore (e.g. a vendor paid against W2 later gets
    // reassigned to W4, and no one else's plan touches W2) — otherwise the
    // column vanishes and that vendor's actual paid figure is no longer
    // shown anywhere in the table, even though the money itself is
    // untouched server-side.
    Object.values(agingByVendorId).forEach((aging) => {
      Object.keys(aging?.week_actual_paid || {}).forEach((w) => weeks.add(Number(w)));
    });
    return Array.from(weeks).sort((a, b) => a - b);
  }, [planHistory, vendors, agingByVendorId]);

  // P0 (Must Pay)/P1 (Commitment) fixed, then whatever buckets Configuration
  // currently defines, in rotation order — never a hardcoded P2-P5 array
  // (this task's explicit no-hardcode fix).
  const priorityTagOrder = useMemo(() => {
    const order: Record<string, number> = {};
    AUTO_PRIORITY_TAGS.forEach((tag, i) => { order[tag] = i; });
    [...priorityBuckets]
      .sort((a, b) => a.rotation_position - b.rotation_position)
      .forEach((b, i) => { order[b.bucket_key] = AUTO_PRIORITY_TAGS.length + i; });
    return order;
  }, [priorityBuckets]);

  // Every priority tag, P0/P1 fixed then whatever Configuration currently
  // defines — the V-Priority dropdown offers this full range for every
  // vendor regardless of category (Sarath's explicit call, this task).
  const allPriorityTagOptions = useMemo(
    () => [...AUTO_PRIORITY_TAGS, ...[...priorityBuckets].sort((a, b) => a.rotation_position - b.rotation_position).map((b) => b.bucket_key)],
    [priorityBuckets]
  );

  const refreshVendors = async () => setVendors(await listVendors());
  // A logged payment changes this vendor's outstanding/aging tranches —
  // refresh just this one vendor's aging, not the whole batch (found while
  // fixing the N+1 batch-fetch above: this call was missing entirely, so
  // the Aging column silently went stale after every payment until a full
  // page reload).
  const refreshVendorAging = async (vendorId: number) => {
    const aging = await getVendorAging(vendorId);
    setAgingByVendorId((prev) => ({ ...prev, [vendorId]: aging }));
  };
  const refreshPaymentTracking = async () => setPaymentTracking(await getVendorPaymentTracking());
  const refreshPlanHistory = async () => setPlanHistory(await getPlanRuns());
  const refreshNm2MinFunds = async () => {
    const data = await getAllVendorMinFundsRequired(hasGeneratedThisCycle ? undefined : planningMonth || undefined);
    const byVendor: Record<number, number> = {};
    data.breakdown.forEach((r) => { byVendor[r.vendor_id] = r.required_amount; });
    setNm2MinFundsByVendorId(byVendor);
  };

  const loadAll = async () => {
    setLoadError('');
    setIsLoadingAll(true);
    try {
      const [vendorList, tracking, history, buckets] = await Promise.all([
        listVendors(),
        getVendorPaymentTracking(),
        getPlanRuns(),
        getPriorityBuckets(),
      ]);
      setVendors(vendorList);
      setPaymentTracking(tracking);
      setPlanHistory(history);
      setPriorityBuckets(buckets);

      const agingList = await getAllVendorsAging();
      setAgingByVendorId(Object.fromEntries(agingList.map((a) => [a.vendor_id, a])));

      const generated = history.plan_runs.length > 0;
      const resolvedMonth = generated ? (history.plan_runs[0].month || '').slice(0, 7) : planningMonth;
      if (generated) {
        setPlanningMonth(resolvedMonth);
        setFundsInputEnabled(true);
        // Card 1's own number is otherwise session-only state (test_ui.html
        // just leaves it in the DOM once computed) — a page reload after a
        // plan already exists this cycle must still show it, read-only,
        // since the "Calculate" button that would normally set it is
        // deliberately hidden once generated (docs/14: never re-shown).
        getMinimumFundsRequired(resolvedMonth).then((d) => setFundsRequiredFigure(d.total)).catch(() => {});

        // Bug fix (this task): Available Funds / Funds Left went blank on
        // every reload even though a plan already exists — nothing ever
        // restored them from the plan_run's own stored figures. The latest
        // plan_run's funds_figure IS what Finance entered last
        // (PlanRun.funds_figure, set by generate-plan-and-weekly-view).
        //
        // Funds Left fix: leftover_remaining is now persisted on the same
        // row (allocator.py's real post-allocation figure, not an
        // approximation) — read it back directly instead of recomputing
        // funds_figure minus overridden vendors, which ignored what the
        // bucket-ceiling allocation actually spent on every other vendor.
        // NULL on a PlanRun row that predates this column — shown as "—"
        // rather than a fabricated number; self-heals on next Generate.
        const latestRun = history.plan_runs[history.plan_runs.length - 1];
        if (latestRun.funds_figure != null) {
          setAvailableFunds(String(Math.round(latestRun.funds_figure)));
        }
        setFundsLeft(latestRun.leftover_remaining);
      }
      const minFunds = await getAllVendorMinFundsRequired(generated ? undefined : planningMonth || undefined);
      const byVendor: Record<number, number> = {};
      minFunds.breakdown.forEach((r) => { byVendor[r.vendor_id] = r.required_amount; });
      setNm2MinFundsByVendorId(byVendor);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setIsLoadingAll(false);
    }
  };

  useEffect(() => {
    loadAll();
    getVendorCategories().then(setCategoryOptions).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  // Planning month is no longer picked here (Main-tab upload-confirm task,
  // section 4) — Finance confirms it once in the upload confirm modal, and
  // this tab just reads back whatever _resolve_planning_month() would
  // currently fall back to (latest plan_run this cycle, else the persisted
  // config value from that confirm). Refetched on every refreshSignal bump
  // so a fresh upload's confirmed month shows up here without a reload.
  // Never overrides a value already restored from an existing plan_run this
  // cycle (loadAll's own generated-branch already set that first and wins).
  useEffect(() => {
    if (hasGeneratedThisCycle) return;
    getCurrentPlanningMonth()
      .then((d) => { if (d.planning_month) setPlanningMonth(d.planning_month); })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  useEffect(() => {
    if (!planningMonth) return;
    getWeeksInMonth(planningMonth).then((d) => setWeeksInMonth(d.weeks)).catch(() => {});
  }, [planningMonth]);

  useEffect(() => {
    if (!openColumnFilter) return;
    const handler = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-column-filter]')) setOpenColumnFilter(null);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [openColumnFilter]);

  const planRunIdsKey = (planHistory?.plan_runs || []).map((p) => p.plan_run_id).join(',');
  useEffect(() => {
    if (!planHistory) return;
    setCollapsedPlanIds(new Set(planHistory.plan_runs.slice(0, -1).map((p) => p.plan_run_id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planRunIdsKey]);

  const handleCalcFundsRequired = async () => {
    if (!planningMonth) {
      onNotify?.('Pick a planning month first.', 'warning');
      return;
    }
    try {
      const data = await getMinimumFundsRequired(planningMonth);
      setFundsRequiredFigure(data.total);
      setFundsInputEnabled(true);
      if (data.planning_month_warning) onNotify?.(data.planning_month_warning, 'warning');
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  const handleGeneratePlan = async () => {
    const availableFundsNum = Number(moneyDigits(availableFunds)) || 0;
    if (!hasGeneratedThisCycle && !planningMonth) {
      onNotify?.('Pick a planning month and calculate Minimum Funds Required first.', 'warning');
      return;
    }
    setIsGenerating(true);
    try {
      const data = await generatePlanAndWeeklyView(availableFundsNum, hasGeneratedThisCycle ? undefined : planningMonth);
      // Bug fix (this task): funds_left_for_regeneration is a different,
      // pre-allocation figure (available_funds minus manual overrides only —
      // always equals Expected Funds when nothing's overridden). The real
      // "how much is actually left after this plan's allocation" figure is
      // allocator.py's own leftover_remaining.
      setFundsLeft(data.plan.leftover_remaining);
      const rich: Record<number, PlanAllocationRow> = {};
      data.plan.allocations.forEach((row) => { rich[row.vendor_id] = row; });
      setRichPlanByVendorId(rich);
      await Promise.all([refreshPlanHistory(), refreshNm2MinFunds()]);
      onNotify?.(`Plan ${hasGeneratedThisCycle ? 'regenerated' : 'generated'} — ${data.plan.allocations.length} vendors.`, 'success');
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    } finally {
      setIsGenerating(false);
    }
  };

  // Clean confirm-popup surfaces (ConfirmModal) replace the native
  // window.confirm() dialog for Delete Plan and — new — Finalize Plan;
  // `pendingDeletePlanRunId` carries which plan the Trash icon targeted,
  // `showFinalizeConfirm` needs no extra data since Finalize always targets
  // the current cycle's latest plan.
  const [pendingDeletePlanRunId, setPendingDeletePlanRunId] = useState<number | null>(null);
  const [showFinalizeConfirm, setShowFinalizeConfirm] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const confirmDeletePlanRun = async (planRunId: number) => {
    setPendingDeletePlanRunId(null);
    try {
      // Bug fix (this task): richPlanByVendorId (the companion panel's
      // vendor pool) is only ever populated by handleGeneratePlan's own
      // response, never rebuilt from plan history — captured BEFORE the
      // delete/refetch below, since afterward there's no way to tell
      // whether the plan_run that just got deleted was the latest one.
      // Deleting the only plan (or the current latest, even with an older
      // one still standing) invalidates this cache entirely — an older
      // plan_run's own allocations were never "rich" to begin with (see
      // this field's own declaration comment), so there is nothing valid
      // left to fall back to. Deleting a non-latest plan leaves the
      // still-standing latest generate's pool untouched and valid.
      const deletedWasLatest = latestPlanRun?.plan_run_id === planRunId;
      await deletePlanRun(planRunId);
      // The delete may have cleared an override server-side (if that
      // plan_run was the latest one) — refetch both so the table's Override
      // column and the Reconsider freeze-on-override rule reflect it right
      // away, same as every other override-clearing action in this file.
      const [freshHistory] = await Promise.all([getPlanRuns(), refreshVendors()]);
      setPlanHistory(freshHistory);
      if (deletedWasLatest) setRichPlanByVendorId({});
      onNotify?.(`Deleted plan #${planRunId}.`, 'success');
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  // Opens the confirm popup; the actual finalize call only fires from
  // confirmFinalize() below once Finance clicks through it.
  const handleFinalize = () => {
    // Root cause of "Finalize doesn't reflect into Payments": nothing
    // stopped Finance from clicking Finalize before any plan existed (or
    // before this cycle's plan was even loaded) — the backend still
    // returns ok:true in that case (there's simply nothing to snapshot),
    // so every Budget silently stays 0 with no visible error.
    if (!hasGeneratedThisCycle) {
      onNotify?.('Generate a plan first — there is nothing to finalize yet.', 'warning');
      return;
    }
    setShowFinalizeConfirm(true);
  };

  const confirmFinalize = async () => {
    // Sarath's explicit call (this task): Finalize never blocks on a
    // shortfall — the backend now always publishes the Budget snapshot
    // (finalize_plan()) regardless of `ok`, so the confirm card always
    // closes here too. `ok`/`over_by` are still surfaced, just as a
    // warning toast instead of a modal that stays open and refuses to
    // proceed (CLAUDE.md rule 2: suggestion, never enforce).
    //
    // Button-status task (Finance's ask): a full-page loading overlay runs
    // for the whole finalize+refresh round trip, not just the API call
    // itself — Finance's complaint was the screen looking frozen/unclear
    // while this runs, and refreshPaymentTracking() is part of the same
    // wait as far as they're concerned.
    setShowFinalizeConfirm(false);
    setIsFinalizing(true);
    try {
      const data = await finalizeNewModel2();
      await refreshPaymentTracking();
      if (data.ok) {
        onNotify?.(
          `Plan finalized — ${formatMoney(data.total_committed)} of ${formatMoney(data.available_funds)} available funds committed. ` +
            `Budget updated for ${data.vendor_count} vendor(s).`,
          'success'
        );
      } else {
        onNotify?.(
          `Plan finalized despite a shortfall of ${formatMoney(data.over_by)} — Budget updated for ${data.vendor_count} vendor(s). ` +
            'This is a suggestion, not a block.',
          'warning'
        );
      }
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    } finally {
      setIsFinalizing(false);
    }
  };

  // Opens the confirm popup; the actual reset call only fires from
  // confirmReset() below once Finance clicks through it.
  const handleReset = () => setShowResetConfirm(true);

  const confirmReset = async () => {
    setShowResetConfirm(false);
    try {
      await resetNewModel2Cycle();
      // Back to the exact state loadAll() produces before any plan exists
      // this cycle: no Min Funds figure, funds input disabled, nothing
      // generated. These four aren't restored by loadAll() alone (it only
      // ever sets them inside its own "a plan already exists" branch), so
      // clear them explicitly here.
      setFundsRequiredFigure(null);
      setFundsInputEnabled(false);
      setAvailableFunds('');
      setFundsLeft(null);
      setRichPlanByVendorId({});
      setReconsiderYes({});
      await loadAll();
      onNotify?.('Planning cycle reset — start over from Calculate Minimum Funds Required.', 'success');
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  const handleDownloadExport = async () => {
    setIsDownloadingExport(true);
    try {
      const { blob, filename } = await downloadFinalizedPlanExport();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename ?? 'Vendor Payment Plan.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    } finally {
      setIsDownloadingExport(false);
    }
  };

  const handleDownloadMinFundsVerification = async () => {
    setIsDownloadingMinFunds(true);
    try {
      const { blob, filename } = await downloadMinFundsVerificationExport();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename ?? 'Min Funds Verification.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    } finally {
      setIsDownloadingMinFunds(false);
    }
  };

  const handleOverrideChange = async (vendor: Vendor, overrideAmount: number | null) => {
    const allocation = latestAllocationByVendorId.get(vendor.id);
    if (!allocation) {
      onNotify?.(`Can't save an override for ${vendor.vendor_name} — no plan allocation this cycle. Generate/regenerate the plan first.`, 'warning');
      return;
    }
    try {
      const resp = await patchOverride(allocation.plan_allocation_id, overrideAmount);
      await Promise.all([refreshVendors(), refreshPlanHistory()]);
      if (resp.funds_warning) {
        const w = resp.funds_warning;
        onNotify?.(`Committed amounts (${formatMoney(w.total_committed)}) exceed available funds (${formatMoney(w.available_funds)}) by ${formatMoney(w.over_by)}. This is a suggestion, not a block.`, 'warning');
      }
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  const handleDistributionChange = async (vendor: Vendor, week: number, rawAmount: string) => {
    const allocation = latestAllocationByVendorId.get(vendor.id);
    if (!allocation) return;
    const digits = moneyDigits(rawAmount);
    const amount = digits === '' ? 0 : Number(digits);
    try {
      await patchWeekDistribution(allocation.plan_allocation_id, { [String(week)]: amount });
      await refreshPlanHistory();
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  const handleVendorFieldChange = async (vendor: Vendor, field: string, value: unknown) => {
    try {
      await patchVendor(vendor.id, field, value);
      // Payments table shows this vendor's own Category badge too — without
      // this, a category/tag edit in Planning only ever showed up there
      // after an unrelated full reload.
      await Promise.all([refreshVendors(), refreshPaymentTracking()]);
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  // Category and V-Priority are two independent DB fields but one Finance
  // concept — changing either one keeps the other in lockstep (Must
  // Pay=P0, Commitment=P1, Inactive=P5, Normal=P2/P3/P4 default P2).
  //
  // Bug fix (this task — "can't change Commitment vendor to Normal, the
  // dropdown doesn't work"): this used to ALSO independently re-derive the
  // implied sibling tag client-side and fire a SECOND patchVendor() call
  // for it. That was always redundant — backend/configuration/
  // vendor_edits.py::update_vendor_field() already derives and writes the
  // sibling field atomically in the SAME single-field PATCH (_derive_sibling(),
  // returned as sibling_field/sibling_old_value/sibling_new_value on the
  // response) — and it was actively harmful: the second call re-opened and
  // re-saved the real Excel file a second time for no reason, entirely on
  // the strength of a comparison against a value already stale by the time
  // it ran (`vendor.priority_tag` captured before the first await). If that
  // second, unnecessary call ever failed (a transient error, a concurrent
  // edit elsewhere), the catch block fired and `refreshVendors()` never ran
  // — so even though the category change had ALREADY succeeded server-side,
  // the row kept showing the OLD category until the next full reload,
  // which is exactly what looked like "the dropdown doesn't work." One
  // single-field PATCH now does the whole job; the backend's own response
  // already tells us if a sibling/commitment_months flag needs surfacing.
  const handleCategoryChange = async (vendor: Vendor, newCategory: string) => {
    try {
      const result = await patchVendor(vendor.id, 'category', newCategory);
      await Promise.all([refreshVendors(), refreshPaymentTracking()]);
      if (result.commitment_months_warning) {
        onNotify?.(result.commitment_months_warning, 'warning');
      }
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  const handlePriorityTagChange = async (vendor: Vendor, newTag: string) => {
    try {
      const result = await patchVendor(vendor.id, 'priority_tag', newTag);
      await Promise.all([refreshVendors(), refreshPaymentTracking()]);
      if (result.commitment_months_warning) {
        onNotify?.(result.commitment_months_warning, 'warning');
      }
    } catch (e) {
      onNotify?.(friendlyErrorMessage(e), 'error');
    }
  };

  const reconsiderEnabled = (vendor: Vendor) => vendor.override_amount == null;
  const toggleReconsider = (id: number) => setReconsiderYes((prev) => ({ ...prev, [id]: !prev[id] }));
  const toggleReconsiderAll = () => {
    const enabledIds = vendors.filter(reconsiderEnabled).map((v) => v.id);
    if (!enabledIds.length) return;
    const next = !enabledIds.every((id) => reconsiderYes[id]);
    setReconsiderYes((prev) => {
      const copy = { ...prev };
      enabledIds.forEach((id) => { copy[id] = next; });
      return copy;
    });
  };

  // Untagged rank: one past every real tag (mirrors backend/weekly_planning/
  // planner.py's own within-week-order convention — tag_rank = {P0:0, P1:1,
  // ...bucket_order}, untagged_rank = len(tag_rank) — reused here rather
  // than a second, independently-invented fallback constant).
  const untaggedPriorityRank = Object.keys(priorityTagOrder).length;

  const columnFilterValues = {
    vendor_name: (v: Vendor) => v.vendor_name,
    erp_code: (v: Vendor) => v.erp_code,
    assigned_week: (v: Vendor) => (v.assigned_week ? `W${v.assigned_week}` : ''),
    outstanding: (v: Vendor) => String(v.live_outstanding_balance ?? ''),
  } satisfies Record<string, (v: Vendor) => string>;

  const filteredPlanningVendors = vendors
    .filter((v) => {
      const q = searchQuery.toLowerCase();
      const matchesSearch = v.vendor_name.toLowerCase().includes(q) || v.erp_code.toLowerCase().includes(q);
      const matchesCategory = categoryFilters.size === 0 || categoryFilters.has(v.category as VendorCategory);
      // Bug fix (this task): a vendor whose aging hasn't loaded yet (client
      // cache gap, e.g. added mid-session without a full refetch) used to
      // silently vanish under ANY active aging filter, since undefined
      // oldest_bucket fell back to '' — which never matches a real bucket
      // chip. Not-yet-loaded now matches regardless of the filter (never
      // hides data due to a cache gap); a vendor whose aging HAS loaded but
      // genuinely has no bucket (fully paid, no outstanding balance) still
      // correctly matches nothing, same as before.
      const vendorAging = agingByVendorId[v.id];
      const matchesAging =
        agingBucketFilters.size === 0 || !vendorAging || agingBucketFilters.has(vendorAging.oldest_bucket || '');
      const matchesPriority = priorityTagFilters.size === 0 || priorityTagFilters.has(displayedPriorityTag(v, priorityBuckets));
      const matchesOverridden = !overriddenOnly || (latestAllocationByVendorId.get(v.id)?.override_amount != null);
      const matchesColumnFilters = Object.entries(columnFilters).every(([col, needle]) => {
        if (!needle.trim()) return true;
        const getter = columnFilterValues[col as keyof typeof columnFilterValues];
        return getter ? getter(v).toLowerCase().includes(needle.trim().toLowerCase()) : true;
      });
      return matchesSearch && matchesCategory && matchesAging && matchesPriority && matchesOverridden && matchesColumnFilters;
    })
    .sort((a, b) => (priorityTagOrder[a.priority_tag || ''] ?? untaggedPriorityRank) - (priorityTagOrder[b.priority_tag || ''] ?? untaggedPriorityRank));

  const paymentRowsByVendorId = useMemo(() => {
    const map = new Map<number, VendorPaymentTracking>();
    paymentTracking.forEach((r) => map.set(r.vendor_id, r));
    return map;
  }, [paymentTracking]);


  const paymentRowsSorted = [...paymentTracking].sort(
    (a, b) => categoryValues.indexOf(a.category as VendorCategory) - categoryValues.indexOf(b.category as VendorCategory)
  );

  const selectedVendor = selectedVendorId != null ? vendors.find((v) => v.id === selectedVendorId) || null : null;
  const planVendorsForCompanion = Object.keys(richPlanByVendorId)
    .map((id) => vendors.find((v) => v.id === Number(id)))
    .filter((v): v is Vendor => !!v);

  const th = 'py-2 px-2.5 whitespace-nowrap';
  const td = 'py-2.5 px-2.5 whitespace-nowrap';
  // Lighter chrome for the always-visible tag dropdowns/inputs (Category,
  // V-Priority, Assigned Week, Commitment Months) — borderless until
  // hover/focus so the table reads as data-with-inline-edit rather than a
  // form, matching the demo's plain-badge-until-clicked look. Same
  // onChange/onBlur handlers underneath — purely visual.
  const tagFieldCls = 'w-full bg-transparent border border-transparent hover:border-gray-200 rounded px-1.5 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41] transition-colors';

  // One column-group per plan_run, oldest first ("Plan 1", "Plan 2", …) —
  // every Generate/Regenerate click adds a new one instead of overwriting
  // the last (docs/06 plan-history feature). Only the latest starts
  // expanded (collapsedPlanIds, above); Override is a live editable input
  // only on the truly-latest plan_run — every older one shows its own
  // frozen required_amount_snapshot-based %, read-only.
  const planRuns = planHistory?.plan_runs || [];
  const planGroupHeader = (() => {
    if (planRuns.length === 0) {
      return {
        row1: <th colSpan={4} className={`${th} border-l border-emerald-100 text-center text-gray-400`}>No plan generated yet</th>,
        row2: (
          <React.Fragment>
            <th className={`${th} border-l border-emerald-100 text-right`}>Suggested %</th>
            <th className={`${th} text-right`}>Suggested Amt</th>
            <th className={`${th} text-right`}>Override %</th>
            <th className={`${th} text-right`}>Override Amt</th>
          </React.Fragment>
        ),
      };
    }
    const row1: React.ReactNode[] = [];
    const row2: React.ReactNode[] = [];
    planRuns.forEach((run, idx) => {
      const n = idx + 1;
      if (collapsedPlanIds.has(run.plan_run_id)) {
        row1.push(
          <th key={run.plan_run_id} rowSpan={2} className={`${th} border-l border-emerald-100 text-center text-gray-400 cursor-pointer`}
            title={`Plan ${n} — click to expand`} onClick={() => togglePlanCollapse(run.plan_run_id)}>
            Plan {n} ▸
          </th>
        );
        return;
      }
      row1.push(
        <th key={run.plan_run_id} colSpan={4} className={`${th} border-l border-emerald-100`}>
          <span className="flex items-center justify-between gap-2">
            <span className="cursor-pointer" title="Click to collapse" onClick={() => togglePlanCollapse(run.plan_run_id)}>
              Plan {n} ▾
            </span>
            <button
              type="button"
              title={`Permanently delete Plan ${n}`}
              onClick={(e) => { e.stopPropagation(); setPendingDeletePlanRunId(run.plan_run_id); }}
              className="text-gray-400 hover:text-red-600 cursor-pointer"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </span>
        </th>
      );
      row2.push(
        <React.Fragment key={run.plan_run_id}>
          <th className={`${th} border-l border-emerald-100 text-right`}>Suggested %</th>
          <th className={`${th} text-right`}>Suggested Amt</th>
          <th className={`${th} text-right`}>Override %</th>
          <th className={`${th} text-right`}>Override Amt</th>
        </React.Fragment>
      );
    });
    return { row1, row2 };
  })();
  // 11 fixed columns (Vendor..Aging's 3 sub-columns, Amt paid, Reconsider)
  // + one group of 4 (Suggested/Override) per expanded plan_run, 1 per
  // collapsed sliver, or 4 for the "no plan yet" placeholder + week columns.
  const planGroupColCount = planRuns.length === 0
    ? 4
    : planRuns.reduce((sum, r) => sum + (collapsedPlanIds.has(r.plan_run_id) ? 1 : 4), 0);
  const totalTableCols = 11 + planGroupColCount + weekColumns.length;

  return (
    <div className="flex flex-col gap-5 w-full py-2">
      {loadError && <p className="text-xs text-red-600">ERROR loading planning data: {loadError}</p>}

      {/* Vendors overview — logo + label sit on one line, the number is its
          own big line below (Sarath's call: was icon-on-top-of-label-on-top-
          of-tiny-number, stacked three deep; text-2xl makes the actual
          figure the visually dominant thing on the card, not the icon). */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-6">
        <div className="bg-white border border-gray-200/90 rounded-xl p-4 shadow-2xs flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-emerald-50 text-[#107c41] flex items-center justify-center border border-emerald-200 shrink-0">
              <Users className="w-4 h-4 text-[#107c41]" />
            </div>
            <span className="text-xs text-gray-500 font-medium">Total Vendors</span>
          </div>
          <span className="text-2xl font-bold text-gray-900 tracking-tight">{vendors.length}</span>
        </div>
        <div className="bg-white border border-gray-200/90 rounded-xl p-4 shadow-2xs flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-[#fdeaea] text-[#b42318] flex items-center justify-center border border-red-100 shrink-0">
              <AlertCircle className="w-4 h-4 text-[#b42318]" />
            </div>
            <span className="text-xs text-gray-500 font-medium">Must Pay</span>
          </div>
          <span className="text-2xl font-bold text-gray-900 tracking-tight">{vendors.filter((v) => v.category === VENDOR_CATEGORY.MUST_PAY).length}</span>
        </div>
        <div className="bg-white border border-gray-200/90 rounded-xl p-4 shadow-2xs flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-[#f2eafd] text-[#6b3fa0] flex items-center justify-center border border-purple-100 shrink-0">
              <Handshake className="w-4 h-4 text-[#6b3fa0]" />
            </div>
            <span className="text-xs text-gray-500 font-medium">Commitment</span>
          </div>
          <span className="text-2xl font-bold text-gray-900 tracking-tight">{vendors.filter((v) => v.category === VENDOR_CATEGORY.COMMITMENT).length}</span>
        </div>
        <div className="bg-white border border-gray-200/90 rounded-xl p-4 shadow-2xs flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-blue-50 text-[#0c447c] flex items-center justify-center border border-blue-100 shrink-0">
              <Layers className="w-4 h-4 text-[#0c447c]" />
            </div>
            <span className="text-xs text-gray-500 font-medium">Normal Vendors</span>
          </div>
          <span className="text-2xl font-bold text-gray-900 tracking-tight">{vendors.filter((v) => v.category === VENDOR_CATEGORY.NORMAL).length}</span>
        </div>
        {/* Added per Finance's ask (this task): count card for Inactive
            (P5) vendors, same style/pattern as Must Pay/Commitment/Normal. */}
        <div className="bg-white border border-gray-200/90 rounded-xl p-4 shadow-2xs flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-gray-100 text-gray-500 flex items-center justify-center border border-gray-200 shrink-0">
              <UserX className="w-4 h-4 text-gray-500" />
            </div>
            <span className="text-xs text-gray-500 font-medium">Inactive Vendors</span>
          </div>
          <span className="text-2xl font-bold text-gray-900 tracking-tight">{vendors.filter((v) => v.category === VENDOR_CATEGORY.INACTIVE).length}</span>
        </div>
      </div>

      {/* Three-card cycle flow (CLAUDE.md rule 4, docs/14's fixed order:
          Min Funds -> Available Funds -> Funds Left). Planning Month card
          removed per Finance's ask (this task) — Planning Month is still
          tracked/used internally, just no longer shown as its own card. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* No icon (Sarath's call) — stacked top-to-bottom: top band
            (amount) ~70% of the card's height, bottom band the full-bleed
            green "Cal Min Funds" button spanning the entire width, ~30% of
            the height (no divider, no inset — the button IS the bottom
            30%). overflow-hidden clips the button's flat top edge to the
            card's own rounded bottom corners. */}
        <div className="bg-white border border-gray-200/90 rounded-xl shadow-2xs flex flex-col overflow-hidden">
          <div className="flex-7 min-h-0 flex flex-col justify-center gap-1 p-4">
            <span className="text-xs text-gray-500 font-medium">Minimum Funds Required</span>
            <span className="text-xl font-bold text-gray-900 tracking-tight truncate">{formatMoney(fundsRequiredFigure)}</span>
          </div>
          {/* Always visible now (Sarath's explicit call, overriding the
              earlier "hidden once generated, never re-shown" behavior) —
              safe to click any time: it just recalculates the same Min
              Funds Required figure for the current (locked, once
              generated) planning month, nothing else changes. */}
          <button
            type="button"
            onClick={handleCalcFundsRequired}
            title="Calculate Minimum Funds Required"
            className="flex-3 min-h-0 w-full p-[5%] flex items-center justify-center text-center bg-[#107c41] hover:bg-[#0d6535] text-white text-sm font-bold whitespace-nowrap cursor-pointer"
          >
            Cal Min Funds
          </button>
        </div>

        {/* No icon — top 30% label, bottom 70% input (Sarath's call). */}
        <div className="bg-white border border-gray-200/90 rounded-xl shadow-2xs flex flex-col p-4 gap-1">
          <div className="flex-3 flex items-center">
            <span className="text-xs text-gray-500 font-medium">Expected Funds</span>
          </div>
          <div className="flex-7 flex items-center">
            <input
              ref={fundsInputRef}
              type="text"
              inputMode="numeric"
              disabled={!fundsInputEnabled}
              value={availableFunds ? `₹${Number(availableFunds).toLocaleString('en-IN')}` : ''}
              placeholder={fundsInputEnabled ? '₹0' : 'calc funds first'}
              onChange={handleFundsInput}
              className="w-full text-xl font-bold text-gray-900 bg-gray-50 rounded-lg px-2 py-1.5 focus:outline-none disabled:text-gray-400 truncate"
            />
          </div>
        </div>

        <div className="bg-white border border-gray-200/90 rounded-xl p-4 shadow-2xs flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-emerald-50 text-emerald-700 flex items-center justify-center border border-emerald-100 shrink-0">
              <PiggyBank className="w-4 h-4 text-emerald-600" />
            </div>
            <span className="text-xs text-gray-500 font-medium" title="Funds left over after this plan's actual allocation, not funds available for the next regeneration.">Funds Left</span>
          </div>
          <span className="text-xl font-bold text-gray-900 tracking-tight">{formatMoney(fundsLeft)}</span>
        </div>
      </div>

      {/* Control bar: search/filter (left) + Generate/Regenerate + Finalize (right) */}
      <div className="bg-white border border-gray-200/90 rounded-xl p-3 shadow-2xs flex flex-col sm:flex-row items-center justify-between gap-3">
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative flex-1 sm:w-56">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search vendor by name or ERP code..."
              className="w-full pl-9 pr-3 py-1.5 bg-gray-50/80 border border-gray-200 rounded-lg text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41]"
            />
          </div>
          <div className="relative group">
            <button
              type="button"
              title="Filters"
              className={`relative p-2 border rounded-lg text-gray-600 bg-gray-50 cursor-pointer ${activeFilterCount > 0 ? 'border-[#107c41]' : 'border-gray-200'}`}
            >
              <SlidersHorizontal className="w-4 h-4" />
              {activeFilterCount > 0 && (
                <span className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-[#107c41] text-white text-[9px] font-bold flex items-center justify-center">
                  {activeFilterCount}
                </span>
              )}
            </button>
            {/* Structured 3-column filter panel (Category / Aging bucket /
                Priority tag) — hidden until hovering the icon OR the panel
                itself (group-hover covers both, same `group` div). Fixed
                overall width + CSS Grid gives each column equal width
                regardless of its chips' natural size, so the three sections
                line up cleanly instead of raggedly sizing to content.
                "Clear all" lives in its own header row (not squeezed next
                to a column label) so it never gets clipped. Colors match
                the same conventions used elsewhere in this table
                (CATEGORY_BADGE_CLASS / AGING_BUCKET_BADGE_CLASS / the tag's
                implied category color). */}
            <div className="hidden group-hover:block absolute z-50 top-full left-0 mt-1.5 bg-white border border-gray-200/90 rounded-xl shadow-2xs w-[34rem] max-w-[90vw] overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100">
                <span className="text-xs font-bold text-gray-700">Filters</span>
                {activeFilterCount > 0 && (
                  <button type="button" onClick={clearAllFilters} className="text-[11px] text-gray-400 hover:text-red-600 font-medium cursor-pointer">
                    Clear all
                  </button>
                )}
              </div>
              <div className="grid grid-cols-3 divide-x divide-gray-100">
                <div className="flex flex-col gap-2.5 p-4 min-w-0">
                  <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">Category</span>
                  <div className="flex flex-wrap gap-1.5">
                    {categoryValues.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => toggleInSet(setCategoryFilters, c)}
                        className={`px-2.5 py-1 rounded-full text-[11px] font-semibold cursor-pointer border ${categoryFilters.has(c) ? 'border-current' : 'border-transparent opacity-50'} ${categoryBadgeClass(c)}`}
                      >
                        {categoryLabelByValue[c]}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex flex-col gap-2.5 p-4 min-w-0">
                  <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">Aging bucket</span>
                  <div className="flex flex-wrap gap-1.5">
                    {AGING_BUCKET_OPTIONS.map((b: string) => (
                      <button
                        key={b}
                        type="button"
                        onClick={() => toggleInSet(setAgingBucketFilters, b)}
                        className={`px-2.5 py-1 rounded-full text-[11px] font-semibold cursor-pointer border ${agingBucketFilters.has(b) ? 'border-current' : 'border-transparent opacity-50'} ${AGING_BUCKET_BADGE_CLASS[b as keyof typeof AGING_BUCKET_BADGE_CLASS]}`}
                      >
                        {b}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex flex-col gap-2.5 p-4 min-w-0">
                  <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">Priority tag</span>
                  <div className="flex flex-wrap gap-1.5">
                    {allPriorityTagOptions.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => toggleInSet(setPriorityTagFilters, tag)}
                        className={`px-2.5 py-1 rounded-full text-[11px] font-semibold cursor-pointer border ${priorityTagFilters.has(tag) ? 'border-current' : 'border-transparent opacity-50'} ${categoryBadgeClass(impliedCategoryForTag(tag, priorityBuckets))}`}
                      >
                        {tag}
                      </button>
                    ))}
                  </div>
                  <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide mt-1">Override</span>
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => setOverriddenOnly((prev) => !prev)}
                      className={`px-2.5 py-1 rounded-full text-[11px] font-semibold cursor-pointer border ${overriddenOnly ? 'border-current bg-gray-100 text-gray-700' : 'border-transparent opacity-50 bg-gray-100 text-gray-700'}`}
                    >
                      Overridden only
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 w-full sm:w-auto justify-end flex-nowrap">
          <button
            onClick={handleGeneratePlan}
            disabled={isGenerating || !fundsInputEnabled}
            className="px-3 py-1.5 bg-[#107c41] hover:bg-[#0d6535] disabled:opacity-50 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-pointer whitespace-nowrap"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isGenerating ? 'animate-spin' : ''}`} />
            {isGenerating
              ? (hasGeneratedThisCycle ? 'Regenerating…' : 'Generating…')
              : (hasGeneratedThisCycle ? 'Regenerate Plan' : 'Generate Plan')}
          </button>
          <button
            onClick={handleDownloadMinFundsVerification}
            disabled={isDownloadingMinFunds || Object.keys(nm2MinFundsByVendorId).length === 0}
            title={Object.keys(nm2MinFundsByVendorId).length === 0 ? 'Calculate Minimum Funds Required first' : undefined}
            className="px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed text-gray-700 rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-pointer whitespace-nowrap"
          >
            <Download className={`w-3.5 h-3.5 ${isDownloadingMinFunds ? 'animate-bounce' : ''}`} />
            {isDownloadingMinFunds ? 'Downloading…' : 'Verify Min Funds'}
          </button>
          <button
            onClick={handleFinalize}
            disabled={!hasGeneratedThisCycle || isFinalizing}
            title={!hasGeneratedThisCycle ? 'Generate a plan first' : undefined}
            className="px-3 py-1.5 bg-white border border-[#107c41] hover:bg-emerald-50/60 disabled:opacity-50 disabled:cursor-not-allowed text-[#107c41] rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-pointer whitespace-nowrap"
          >
            <CheckCircle2 className="w-3.5 h-3.5" />
            {isFinalizing ? 'Finalizing…' : 'Finalize Plan'}
          </button>
          <button
            onClick={handleDownloadExport}
            disabled={isDownloadingExport || !paymentRowsSorted.some((row) => row.budget > 0)}
            title={!paymentRowsSorted.some((row) => row.budget > 0) ? 'Finalize a plan first' : undefined}
            className="px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed text-gray-700 rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-pointer whitespace-nowrap"
          >
            <Download className={`w-3.5 h-3.5 ${isDownloadingExport ? 'animate-bounce' : ''}`} />
            {isDownloadingExport ? 'Downloading…' : 'Download Excel'}
          </button>
          <button
            onClick={handleReset}
            disabled={!hasGeneratedThisCycle}
            title={!hasGeneratedThisCycle ? 'Nothing generated yet this cycle' : undefined}
            className="px-3 py-1.5 bg-white border border-gray-300 hover:bg-red-50 hover:border-red-300 hover:text-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-gray-700 rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-pointer whitespace-nowrap"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset
          </button>
        </div>
      </div>

      {/* Planning table */}
      <div className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-2xs flex flex-col gap-3">
        <div className="flex items-center gap-2 pb-2 border-b border-gray-100">
          <h3 className="text-sm font-bold text-gray-900">Planning</h3>
          <span className="text-xs font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">{filteredPlanningVendors.length} entries</span>
        </div>
        <div className="table-scroll overscroll-contain max-h-155">
          <table className="w-full text-left text-xs border-separate border-spacing-0">
            <thead className="bg-emerald-50 text-[#107c41] font-semibold sticky top-0 z-30">
              <tr className="border-b border-emerald-100">
                <th rowSpan={2} className={`${th} sticky left-0 z-40 bg-emerald-50`}>
                  Vendor
                  <ColumnFilterIcon column="vendor_name" label="Vendor" active={!!columnFilters.vendor_name}
                    isOpen={openColumnFilter === 'vendor_name'} onToggle={() => setOpenColumnFilter((c) => (c === 'vendor_name' ? null : 'vendor_name'))}
                    value={columnFilters.vendor_name || ''} onChange={(v) => setColumnFilters((prev) => ({ ...prev, vendor_name: v }))} />
                </th>
                <th rowSpan={2} className={th}>
                  ERP code
                  <ColumnFilterIcon column="erp_code" label="ERP code" active={!!columnFilters.erp_code}
                    isOpen={openColumnFilter === 'erp_code'} onToggle={() => setOpenColumnFilter((c) => (c === 'erp_code' ? null : 'erp_code'))}
                    value={columnFilters.erp_code || ''} onChange={(v) => setColumnFilters((prev) => ({ ...prev, erp_code: v }))} />
                </th>
                <th rowSpan={2} className={`${th} min-w-32.5`}>Category</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 min-w-17.5`}>V-Priority</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100`}>
                  Assigned Wk
                  <ColumnFilterIcon column="assigned_week" label="Assigned Wk" active={!!columnFilters.assigned_week}
                    isOpen={openColumnFilter === 'assigned_week'} onToggle={() => setOpenColumnFilter((c) => (c === 'assigned_week' ? null : 'assigned_week'))}
                    value={columnFilters.assigned_week || ''} onChange={(v) => setColumnFilters((prev) => ({ ...prev, assigned_week: v }))} />
                </th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>
                  Outstanding
                  <ColumnFilterIcon column="outstanding" label="Outstanding" active={!!columnFilters.outstanding}
                    isOpen={openColumnFilter === 'outstanding'} onToggle={() => setOpenColumnFilter((c) => (c === 'outstanding' ? null : 'outstanding'))}
                    value={columnFilters.outstanding || ''} onChange={(v) => setColumnFilters((prev) => ({ ...prev, outstanding: v }))} />
                </th>
                <th colSpan={3} className={`${th} border-l border-emerald-100 text-center`}>Aging</th>
                {planGroupHeader.row1}
                {weekColumns.length > 0 && (
                  <th colSpan={weekColumns.length} className={`${th} border-l border-emerald-100 text-center`}>Distribution</th>
                )}
                <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>Amt paid</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100`}>
                  <span className="flex items-center gap-1.5">
                    Reconsider
                    {(() => {
                      const enabledIds = vendors.filter(reconsiderEnabled).map((v) => v.id);
                      const anyEnabled = enabledIds.length > 0;
                      const allYes = anyEnabled && enabledIds.every((id) => reconsiderYes[id]);
                      return (
                        <button
                          type="button"
                          disabled={!anyEnabled}
                          onClick={toggleReconsiderAll}
                          title={anyEnabled ? `Enable all: ${allYes ? 'Yes' : 'No'}` : 'No vendor is eligible to reconsider'}
                          className={`w-6 h-3.5 rounded-full relative ${!anyEnabled ? 'bg-gray-300 cursor-not-allowed' : allYes ? 'bg-[#1a7f4e] cursor-pointer' : 'bg-[#d64545] cursor-pointer'}`}
                        >
                          <span className="absolute top-0.5 w-2.5 h-2.5 rounded-full bg-white" style={{ left: anyEnabled && allYes ? '12px' : '2px' }} />
                        </button>
                      );
                    })()}
                  </span>
                </th>
              </tr>
              <tr className="border-b border-emerald-100">
                <th className={`${th} border-l border-emerald-100`}>Months</th>
                <th className={th}>Bucket</th>
                <th className={`${th} text-right`}>Total Min Funds</th>
                {planGroupHeader.row2}
                {weekColumns.map((w, i) => (
                  <th key={w} className={`${th} text-right ${i === 0 ? 'border-l border-emerald-100' : ''}`}>W{w}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-gray-700">
              {isLoadingAll ? (
                <tr><td colSpan={totalTableCols} className="py-6 text-center text-gray-400 text-xs">Loading…</td></tr>
              ) : filteredPlanningVendors.length === 0 ? (
                <tr><td colSpan={totalTableCols} className="py-6 text-center text-gray-400 text-xs">No vendor planning records match your search filter.</td></tr>
              ) : (
                filteredPlanningVendors.map((vendor) => {
                  const aging = agingByVendorId[vendor.id];
                  const allocation = latestAllocationByVendorId.get(vendor.id);
                  const denom = allocation?.required_amount_snapshot ?? null;
                  const distribution = planHistory?.vendor_week_distribution_plans[String(vendor.id)] || {};
                  // Item 2 (this task): Vendor.week_distribution_plan is
                  // deliberately sticky across regenerations once Finance has
                  // ever hand-edited a week (planner.py's own comment: "must
                  // keep re-deriving fresh ... for as long as Finance hasn't
                  // actually edited a week" — a stored, non-null distribution
                  // is never auto-rescaled). docs/06 only specifies grouping
                  // a vendor's single allocated amount by Assigned Week for
                  // display — it's silent on this further per-vendor,
                  // multi-week manual split, so the fix here is a visible
                  // staleness flag rather than a backend auto-rescale (which
                  // would silently overwrite a manual split Finance made on
                  // purpose). Flag whenever the stored split's total no
                  // longer sums to the vendor's current effective amount
                  // (override, if set, else the freshly-generated suggestion).
                  const distributionValues = Object.values(distribution).filter((v): v is number => typeof v === 'number');
                  const distributionTotal = distributionValues.reduce((sum, v) => sum + v, 0);
                  const currentEffectiveAmount = allocation ? (allocation.override_amount ?? allocation.allocated_amount) : null;
                  const distributionStale =
                    currentEffectiveAmount != null &&
                    distributionValues.length > 0 &&
                    Math.abs(distributionTotal - currentEffectiveAmount) > MONEY_EPSILON;
                  const canReconsider = reconsiderEnabled(vendor);
                  const isYes = !!reconsiderYes[vendor.id];
                  return (
                    <tr key={vendor.id} className="hover:bg-gray-50/70 cursor-pointer" onClick={() => { setSelectedVendorId(vendor.id); setShowPaymentInModal(false); }}>
                      <td className={`${td} sticky left-0 z-10 bg-white font-medium text-gray-900`}>
                        {vendor.vendor_name}
                        {distributionStale && (
                          <span
                            className="inline-block align-text-bottom ml-1"
                            title={`Weekly split (${formatMoney(distributionTotal)}) no longer matches this vendor's current allocation (${formatMoney(currentEffectiveAmount)}) — rebalance the week amounts below.`}
                          >
                            <AlertCircle className="inline w-3 h-3 text-amber-500" />
                          </span>
                        )}
                      </td>
                      <td className={`${td} font-mono text-[11px] text-gray-500`}>{vendor.erp_code}</td>
                      <td className={td} onClick={(e) => e.stopPropagation()}>
                        <select
                          value={vendor.category}
                          onChange={(e) => handleCategoryChange(vendor, e.target.value)}
                          className={`${tagFieldCls} min-w-30 font-semibold ${categoryBadgeClass(vendor.category)}`}
                        >
                          {categoryValues.map((c) => <option key={c} value={c}>{categoryLabelByValue[c]}</option>)}
                        </select>
                      </td>
                      <td className={`${td} border-l border-gray-100`} onClick={(e) => e.stopPropagation()}>
                        {FIXED_TAG_FOR_CATEGORY[vendor.category] ? (
                          // Must Pay/Commitment: fixed, not manually reassignable.
                          <span className="px-1.5 py-1 text-gray-500">{FIXED_TAG_FOR_CATEGORY[vendor.category]}</span>
                        ) : (
                          // Every other category: real choice among its own live bucket
                          // rows (Normal: P2/P3/P4; Inactive: P5 only; a custom category:
                          // whatever Configuration gave it) — defaults to the first.
                          <select
                            value={displayedPriorityTag(vendor, priorityBuckets)}
                            onChange={(e) => handlePriorityTagChange(vendor, e.target.value)}
                            className={tagFieldCls}
                          >
                            {tagsForCategory(vendor.category, priorityBuckets).map((tag) => <option key={tag} value={tag}>{tag}</option>)}
                          </select>
                        )}
                      </td>
                      <td className={`${td} border-l border-gray-100`} onClick={(e) => e.stopPropagation()}>
                        <select
                          value={vendor.assigned_week ?? ''}
                          onChange={(e) => handleVendorFieldChange(vendor, 'assigned_week', e.target.value === '' ? null : Number(e.target.value))}
                          className={tagFieldCls}
                        >
                          <option value="">—</option>
                          {(
                            weeksInMonth != null
                              ? Array.from({ length: weeksInMonth }, (_, i) => i + 1)
                              // Not yet known (docs/17 race fix): offer only the vendor's own
                              // already-assigned week, if any — never a guessed full range.
                              : vendor.assigned_week
                              ? [vendor.assigned_week]
                              : []
                          ).map((w) => <option key={w} value={w}>W{w}</option>)}
                        </select>
                      </td>
                      <td className={`${td} border-l border-gray-100 text-right font-semibold text-gray-900`}>{formatMoney(vendor.live_outstanding_balance)}</td>
                      <td className={`${td} border-l border-gray-100 text-gray-600`}>{aging?.oldest_bucket_months_back ?? '—'}</td>
                      <td className={`${td} text-gray-600`}>{aging?.oldest_bucket || '—'}</td>
                      <td className={`${td} text-right text-gray-600`}>{formatMoney(nm2MinFundsByVendorId[vendor.id])}</td>
                      {planRuns.length === 0 ? (
                        <>
                          <td className={`${td} border-l border-gray-100 text-right text-gray-600`}>—</td>
                          <td className={`${td} text-right font-semibold text-gray-900`}>—</td>
                          <td className={`${td} text-right text-gray-500`}>—</td>
                          <td className={`${td} text-right text-gray-400`}>—</td>
                        </>
                      ) : (
                        planRuns.map((run, idx) => {
                          if (collapsedPlanIds.has(run.plan_run_id)) {
                            return <td key={run.plan_run_id} className={`${td} border-l border-gray-100 text-center text-gray-300`}>⋯</td>;
                          }
                          const isLastPlan = idx === planRuns.length - 1;
                          // The truly-latest plan_run reuses the same `allocation`/
                          // `denom` already resolved above (latestAllocationByVendorId,
                          // the same row handleOverrideChange/patchOverride target) —
                          // every older plan_run resolves its own row + its own
                          // frozen required_amount_snapshot, never today's live figure.
                          const rowAlloc = isLastPlan ? allocation : run.allocations.find((a) => a.vendor_id === vendor.id);
                          const rowDenom = isLastPlan ? denom : rowAlloc?.required_amount_snapshot ?? null;
                          return (
                            <React.Fragment key={run.plan_run_id}>
                              <td className={`${td} border-l border-gray-100 text-right text-gray-600`}>{rowAlloc ? formatPct(rowAlloc.allocated_amount, rowDenom) : '—'}</td>
                              <td className={`${td} text-right font-semibold text-gray-900`}>{rowAlloc ? formatMoney(rowAlloc.allocated_amount) : '—'}</td>
                              {isLastPlan ? (
                                rowAlloc ? (
                                  <OverrideCells
                                    // Remounts whenever the server's own override_amount
                                    // changes (a fresh save, a clamp, a regenerate) so a
                                    // stale typed value can never sit in the box looking
                                    // saved when it isn't (R1) — stays mounted while the
                                    // vendor is simply being typed into.
                                    key={`${vendor.id}-${rowAlloc.plan_allocation_id}-${rowAlloc.override_amount ?? 'none'}`}
                                    suggestedAmount={rowAlloc.allocated_amount}
                                    denom={rowDenom}
                                    currentOverride={rowAlloc.override_amount ?? null}
                                    tdClass={td}
                                    onSave={(amount) => handleOverrideChange(vendor, amount)}
                                  />
                                ) : (
                                  <>
                                    <td className={`${td} text-right text-gray-500`}>—</td>
                                    <td className={`${td} text-right`}>—</td>
                                  </>
                                )
                              ) : (
                                <>
                                  <td className={`${td} text-right text-gray-400`}>{rowAlloc?.override_amount != null ? formatPct(rowAlloc.override_amount, rowDenom) : '—'}</td>
                                  <td className={`${td} text-right text-gray-400`}>{rowAlloc?.override_amount != null ? formatMoney(rowAlloc.override_amount) : '—'}</td>
                                </>
                              )}
                            </React.Fragment>
                          );
                        })
                      )}
                      {weekColumns.map((w, i) => {
                        // A week with a real logged payment shows that actual
                        // paid figure, read-only — the plan for that week is
                        // untouched, only the display changes (docs/06). The
                        // vendor's OWN associated week (Vendor.assigned_week)
                        // is always gold-bordered here, regardless of which
                        // week their amount is actually displayed under this
                        // round — border only, so it composes with the paid
                        // fill when both are true for the same week.
                        const paidVal = aging?.week_actual_paid?.[String(w)];
                        const isPaid = paidVal != null;
                        const isAssociatedWeek = w === vendor.assigned_week;
                        const displayVal = isPaid ? paidVal : distribution[String(w)];
                        const borderCls = isAssociatedWeek ? 'border-2 border-amber-400' : isPaid ? 'border border-emerald-600' : 'border border-gray-200';
                        const fillCls = isPaid ? 'bg-emerald-50 text-emerald-700 font-semibold' : 'bg-transparent';
                        return (
                          <td key={w} className={`${td} text-right ${i === 0 ? 'border-l border-gray-100' : ''}`} onClick={(e) => e.stopPropagation()}>
                            <input
                              // Same remount-on-server-change fix as the override input above.
                              key={`${vendor.id}-${w}-${displayVal ?? 'none'}`}
                              type="text"
                              inputMode="numeric"
                              readOnly={isPaid}
                              title={isPaid ? 'Paid — read-only, the plan for this week is unaffected' : isAssociatedWeek ? "This vendor's own associated week" : undefined}
                              defaultValue={displayVal != null ? Math.round(displayVal).toLocaleString('en-IN') : ''}
                              onBlur={isPaid ? undefined : (e) => handleDistributionChange(vendor, w, e.target.value)}
                              className={`w-20 text-right rounded px-1.5 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 ${borderCls} ${fillCls}`}
                            />
                          </td>
                        );
                      })}
                      <td className={`${td} border-l border-gray-100 text-right text-gray-600`}>{formatMoney(paymentRowsByVendorId.get(vendor.id)?.actual_paid_this_month ?? 0)}</td>
                      <td className={`${td} border-l border-gray-100`} onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          disabled={!canReconsider}
                          onClick={() => toggleReconsider(vendor.id)}
                          title={canReconsider ? `Reconsider: ${isYes ? 'Yes' : 'No'}` : 'Frozen — override set this month'}
                          className={`w-8 h-4 rounded-full relative ${!canReconsider ? 'bg-gray-300 cursor-not-allowed' : isYes ? 'bg-[#1a7f4e] cursor-pointer' : 'bg-[#d64545] cursor-pointer'}`}
                        >
                          <span className="absolute top-0.5 w-3 h-3 rounded-full bg-white" style={{ left: canReconsider && isYes ? '18px' : '2px' }} />
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Payments table */}
      <div className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-2xs flex flex-col gap-3">
        <div className="flex items-center gap-2 pb-2 border-b border-gray-100">
          <h3 className="text-sm font-bold text-gray-900">Payments</h3>
          <span className="text-xs font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">{paymentRowsSorted.length} vendors</span>
        </div>
        <div className="table-scroll overscroll-contain max-h-155">
          <table className="w-full text-left text-xs border-separate border-spacing-0">
            <thead className="bg-emerald-50 text-[#107c41] font-semibold sticky top-0 z-30">
              <tr className="border-b border-emerald-100">
                <th rowSpan={2} className={`${th} sticky left-0 z-40 bg-emerald-50`}>Vendor</th>
                <th rowSpan={2} className={th}>ERP code</th>
                <th rowSpan={2} className={`${th} min-w-30`}>Category</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>Outstanding</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>Budget this month</th>
                <th colSpan={3} className={`${th} border-l border-emerald-100 text-center`}>Actual paid this month</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>Balance this month</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 text-right`}>Balance outstanding</th>
                <th rowSpan={2} className={`${th} border-l border-emerald-100 sticky right-0 z-40 bg-emerald-50`}>Pay</th>
              </tr>
              <tr className="border-b border-emerald-100">
                <th className={`${th} border-l border-emerald-100 text-right`}>Amt</th>
                <th className={`${th} text-right`}>% Min Funds Paid</th>
                <th className={`${th} text-right`}>% Outstanding paid</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-gray-700">
              {isLoadingAll && paymentRowsSorted.length === 0 && (
                <tr><td colSpan={10} className="py-6 text-center text-gray-400 text-xs">Loading…</td></tr>
              )}
              {paymentRowsSorted.map((row) => (
                <tr key={row.vendor_id} className="hover:bg-gray-50/70">
                  <td className={`${td} sticky left-0 z-10 bg-white font-medium text-gray-900`}>{row.vendor_name}</td>
                  <td className={`${td} font-mono text-[11px] text-gray-500`}>{row.erp_code}</td>
                  <td className={td}>
                    <span className={`inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${categoryBadgeClass(row.category)}`}>
                      {categoryLabelByValue[row.category] || row.category}
                    </span>
                  </td>
                  <td className={`${td} border-l border-gray-100 text-right font-semibold text-gray-900`}>{formatMoney(row.outstanding)}</td>
                  <td className={`${td} border-l border-gray-100 text-right text-gray-700`}>{formatMoney(row.budget)}</td>
                  <td className={`${td} border-l border-gray-100 text-right font-semibold text-emerald-700`}>{formatMoney(row.actual_paid_this_month)}</td>
                  <td className={`${td} text-right text-gray-600`}>{formatPct(row.actual_paid_this_month, row.min_funds_required)}</td>
                  <td className={`${td} text-right text-gray-600`}>{formatPct(row.actual_paid_this_month, row.outstanding)}</td>
                  <td className={`${td} border-l border-gray-100 text-right text-gray-700`}>{formatMoney(row.balance)}</td>
                  <td className={`${td} border-l border-gray-100 text-right text-gray-700`}>{formatMoney(row.balance_outstanding)}</td>
                  <td className={`${td} border-l border-gray-100 sticky right-0 z-10 bg-white`}>
                    <button
                      type="button"
                      onClick={() => { setSelectedVendorId(row.vendor_id); setShowPaymentInModal(true); }}
                      className="px-2.5 py-1 bg-[#107c41] hover:bg-[#0d6535] text-white rounded-lg text-[11px] font-semibold cursor-pointer"
                    >
                      Pay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selectedVendor && (
        <VendorDetailModal
          vendor={selectedVendor}
          showPayment={showPaymentInModal}
          planningMonth={planningMonth}
          hasGeneratedThisCycle={hasGeneratedThisCycle}
          effectiveAmount={(() => {
            const a = latestAllocationByVendorId.get(selectedVendor.id);
            return a ? (a.override_amount ?? a.allocated_amount) : null;
          })()}
          onClose={() => setSelectedVendorId(null)}
          onPaymentLogged={async () => { await Promise.all([refreshVendors(), refreshPaymentTracking(), refreshVendorAging(selectedVendor.id)]); }}
          onCommitmentMonthsChange={(value) => handleVendorFieldChange(selectedVendor, 'commitment_months', value)}
          onNotify={onNotify}
        />
      )}

      {pendingDeletePlanRunId !== null && (
        <ConfirmModal
          title="Delete this plan?"
          message="Permanently delete this plan. This cannot be undone."
          confirmLabel="Delete"
          variant="danger"
          onConfirm={() => confirmDeletePlanRun(pendingDeletePlanRunId)}
          onCancel={() => setPendingDeletePlanRunId(null)}
        />
      )}

      {showFinalizeConfirm && (
        <ConfirmModal
          title="Finalize this plan?"
          message="Publishes the Budget for every vendor in this plan to Payments."
          confirmLabel="Finalize"
          onConfirm={confirmFinalize}
          onCancel={() => setShowFinalizeConfirm(false)}
          extra={
            // Proactive preview shown the moment the modal opens, using the
            // signed financeSignedDiff computed above: red if short, green
            // if there's room to spare, nothing if it's an exact match.
            // Finalize itself never blocks on this (CLAUDE.md rule 2 /
            // Sarath's explicit call, this task) — it's informational only.
            financeSignedDiff < -MONEY_EPSILON ? (
              <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5 leading-relaxed">
                <p className="font-semibold">Short by {formatMoney(Math.abs(financeSignedDiff))}.</p>
                <p>Try reducing allocations for Normal/Inactive vendors, or increasing available funds, then Finalize again.</p>
              </div>
            ) : financeSignedDiff > MONEY_EPSILON ? (
              <div className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg p-2.5 leading-relaxed">
                <p className="font-semibold">You&apos;ll have {formatMoney(financeSignedDiff)} left after this plan.</p>
              </div>
            ) : null
          }
        />
      )}

      {showResetConfirm && (
        <ConfirmModal
          title="Reset this cycle's plan?"
          message="Deletes this cycle's plan, every override, and the Min Funds figure. You'll start over from Calculate Minimum Funds Required. Past finalized months and vendor master data are not affected."
          confirmLabel="Reset Plan"
          variant="danger"
          onConfirm={confirmReset}
          onCancel={() => setShowResetConfirm(false)}
        />
      )}

      {/* Full-page loading buffer while Finalize actually runs (Finance's
          explicit ask, this task) — the confirm modal above closes the
          moment Finance clicks through, then this overlay covers the whole
          screen until finalizeNewModel2() + the payment-tracking refresh
          both complete. */}
      {isFinalizing && (
        <div
          role="status"
          aria-live="polite"
          className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center"
        >
          <div className="bg-white rounded-xl shadow-xl px-6 py-5 flex flex-col items-center gap-3">
            <div className="w-10 h-10 border-4 border-emerald-100 border-t-[#107c41] rounded-full animate-spin" />
            <span className="text-sm font-semibold text-gray-700">Finalizing plan…</span>
          </div>
        </div>
      )}

      <CompanionPanel planVendors={planVendorsForCompanion} />
    </div>
  );
};
