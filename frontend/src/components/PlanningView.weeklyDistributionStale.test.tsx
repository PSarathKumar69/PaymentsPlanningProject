import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlanningView } from './PlanningView';

// Item 2 (this task): Vendor.week_distribution_plan is deliberately sticky
// across regenerations once Finance has ever hand-edited a week
// (backend/weekly_planning/planner.py's own comment on this) — it is never
// auto-rescaled when the vendor's total later changes via an override. Since
// docs/06-weekly-planning-regeneration.md is silent on this exact per-vendor
// multi-week manual-split feature (it only specifies grouping one allocated
// amount by a single Assigned Week for display), the fix is a visible
// staleness flag next to the vendor name rather than a backend auto-rescale
// that could silently overwrite a manual split Finance made on purpose. This
// suite reproduces the exact repro: generate, note the week split, override
// the vendor's amount, and confirm the now out-of-sync split is flagged.
const listVendors = vi.fn();
const getVendorAging = vi.fn();
const getAllVendorsAging = vi.fn();
const getVendorCategories = vi.fn();
const getVendorPaymentTracking = vi.fn();
const patchVendor = vi.fn();
vi.mock('../api/vendors', () => ({
  listVendors: () => listVendors(),
  getVendorAging: (...args: unknown[]) => getVendorAging(...args),
  getAllVendorsAging: () => getAllVendorsAging(),
  getVendorCategories: () => getVendorCategories(),
  getVendorPaymentTracking: () => getVendorPaymentTracking(),
  patchVendor: (...args: unknown[]) => patchVendor(...args),
}));

const getPlanRuns = vi.fn();
const getMinimumFundsRequired = vi.fn();
const getAllVendorMinFundsRequired = vi.fn();
const generatePlanAndWeeklyView = vi.fn();
const finalizeNewModel2 = vi.fn();
const getCurrentPlanningMonth = vi.fn();
vi.mock('../api/newModel2', () => ({
  getPlanRuns: () => getPlanRuns(),
  getMinimumFundsRequired: (...args: unknown[]) => getMinimumFundsRequired(...args),
  getAllVendorMinFundsRequired: (...args: unknown[]) => getAllVendorMinFundsRequired(...args),
  generatePlanAndWeeklyView: (...args: unknown[]) => generatePlanAndWeeklyView(...args),
  finalizeNewModel2: () => finalizeNewModel2(),
  getCurrentPlanningMonth: () => getCurrentPlanningMonth(),
}));

const patchOverride = vi.fn();
vi.mock('../api/planAllocations', () => ({
  patchOverride: (...args: unknown[]) => patchOverride(...args),
  patchWeekDistribution: vi.fn(),
}));
vi.mock('../api/planRuns', () => ({ deletePlanRun: vi.fn() }));

const getPriorityBuckets = vi.fn();
vi.mock('../api/configuration', () => ({ getPriorityBuckets: () => getPriorityBuckets() }));

const getWeeksInMonth = vi.fn();
vi.mock('../api/calendar', () => ({ getWeeksInMonth: (...args: unknown[]) => getWeeksInMonth(...args) }));

const VENDOR = {
  id: 1,
  erp_code: 'V001',
  entity: 'Acme',
  vendor_name: 'Acme Traders',
  opening_balance: 200000,
  live_outstanding_balance: 200000,
  category: 'normal',
  commitment_months: null,
  assigned_week: 1,
  payment_status: 'not_paid',
  paid_so_far_this_month: 0,
  score: 50,
  current_aging_bucket: '0-30',
  reconsider: null,
  override_amount: null,
  priority_tag: 'P2',
};

const planRunWith = (overrideAmount: number | null) => ({
  plan_run_id: 1,
  created_at: '2026-07-01T00:00:00',
  month: '2026-08-01',
  model_used: 'new_model_2',
  funds_figure: 100000,
  leftover_remaining: 0,
  min_funds_required: null,
  allocations: [
    { plan_allocation_id: 1, vendor_id: 1, assigned_week: 1, within_week_order: 1, allocated_amount: 100000, override_amount: overrideAmount, required_amount_snapshot: 100000 },
  ],
});

const setupMocks = () => {
  listVendors.mockResolvedValue([VENDOR]);
  getVendorPaymentTracking.mockResolvedValue([]);
  getPriorityBuckets.mockResolvedValue([]);
  getAllVendorsAging.mockResolvedValue([]);
  getVendorCategories.mockResolvedValue([{ value: 'normal', label: 'Normal' }]);
  getAllVendorMinFundsRequired.mockResolvedValue({ total: 100000, breakdown: [] });
  getMinimumFundsRequired.mockResolvedValue({
    total: 100000, breakdown: [], planning_month: '2026-08', as_of: '2026-07-01', planning_month_warning: null,
  });
  getWeeksInMonth.mockResolvedValue({ weeks: 4 });
  getCurrentPlanningMonth.mockResolvedValue({ planning_month: '2026-08' });
};

const staleTitleRegex = /weekly split .* no longer matches/i;

describe('PlanningView — weekly distribution staleness flag after an override', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('shows no flag while the stored week split still matches the vendor\'s current allocation', async () => {
    getPlanRuns.mockResolvedValue({
      plan_runs: [planRunWith(null)],
      vendor_week_distribution_plans: { '1': { '1': 100000 } },
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');

    expect(screen.queryByTitle(staleTitleRegex)).not.toBeInTheDocument();
  });

  it('flags the vendor once an override changes the total but the sticky week split does not follow it', async () => {
    // Initial generate: split matches the suggested 100000 exactly.
    getPlanRuns.mockResolvedValueOnce({
      plan_runs: [planRunWith(null)],
      vendor_week_distribution_plans: { '1': { '1': 100000 } },
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');
    expect(screen.queryByTitle(staleTitleRegex)).not.toBeInTheDocument();

    // Override the vendor's amount to 150000 — handleOverrideChange saves it,
    // then refetches plan history. The "regenerated" plan history still
    // carries the OLD, now-stale 100000 week split (planner.py's sticky
    // Vendor.week_distribution_plan — this is the actual repro, not a test
    // artifact: a real regenerate would behave identically since the sticky
    // value is never rescaled server-side).
    patchOverride.mockResolvedValue({ funds_warning: null });
    getPlanRuns.mockResolvedValueOnce({
      plan_runs: [planRunWith(150000)],
      vendor_week_distribution_plans: { '1': { '1': 100000 } },
    });
    listVendors.mockResolvedValueOnce([VENDOR]);

    const overrideAmtInput = screen.getByPlaceholderText('₹1,00,000');
    await userEvent.type(overrideAmtInput, '150000');
    overrideAmtInput.blur();

    await waitFor(() => expect(patchOverride).toHaveBeenCalledWith(1, 150000));
    expect(await screen.findByTitle(staleTitleRegex)).toBeInTheDocument();
  });
});
