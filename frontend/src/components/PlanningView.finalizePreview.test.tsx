import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlanningView } from './PlanningView';

// Finalize modal's proactive signed funds preview (item 1) — separate file
// from PlanningView.test.tsx (which only covers the server-driven shortfall
// warning shown AFTER a real Finalize attempt fails). This suite covers the
// preview shown the moment the modal opens, before Finance clicks anything
// inside it, computed client-side from Available Funds vs. every vendor's
// committed amount (override_amount ?? allocated_amount) — the same pairing
// backend/api/routers/new_model_2.py's own Finalize check uses for
// total_committed, just kept signed instead of floored at 0.
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

vi.mock('../api/planAllocations', () => ({
  patchOverride: vi.fn(),
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

const planRunWith = (fundsFigure: number, allocatedAmount: number) => ({
  plan_run_id: 1,
  created_at: '2026-07-01T00:00:00',
  month: '2026-08-01',
  model_used: 'new_model_2',
  funds_figure: fundsFigure,
  leftover_remaining: 0,
  min_funds_required: null,
  allocations: [
    { plan_allocation_id: 1, vendor_id: 1, assigned_week: 1, within_week_order: 1, allocated_amount: allocatedAmount, override_amount: null, required_amount_snapshot: allocatedAmount },
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

const openFinalizeConfirm = async () => {
  await userEvent.click(await screen.findByRole('button', { name: /finalize plan/i }));
  await screen.findByText('Finalize this plan?');
};

describe('PlanningView — Finalize modal proactive signed funds preview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('shows a red "Short by" card the moment the modal opens when committed exceeds available funds, without calling finalize', async () => {
    getPlanRuns.mockResolvedValue({ plan_runs: [planRunWith(100000, 150000)], vendor_week_distribution_plans: {} });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();

    expect(await screen.findByText(/short by ₹50,000/i)).toBeInTheDocument();
    expect(finalizeNewModel2).not.toHaveBeenCalled();
  });

  it('shows a green "left after this plan" card when available funds exceed committed', async () => {
    getPlanRuns.mockResolvedValue({ plan_runs: [planRunWith(150000, 100000)], vendor_week_distribution_plans: {} });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();

    expect(await screen.findByText(/you'll have ₹50,000 left after this plan/i)).toBeInTheDocument();
    expect(finalizeNewModel2).not.toHaveBeenCalled();
  });

  it('shows no preview card when available funds exactly match committed', async () => {
    getPlanRuns.mockResolvedValue({ plan_runs: [planRunWith(100000, 100000)], vendor_week_distribution_plans: {} });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();

    expect(screen.queryByText(/short by/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/left after this plan/i)).not.toBeInTheDocument();
    expect(finalizeNewModel2).not.toHaveBeenCalled();
  });

  // Button-status task (Finance's ask): a full-page loading buffer while
  // Finalize actually runs, not just a disabled button.
  it('shows a full-page Finalizing overlay while the finalize call is in flight, then clears it', async () => {
    getPlanRuns.mockResolvedValue({ plan_runs: [planRunWith(100000, 100000)], vendor_week_distribution_plans: {} });
    let resolveFinalize: (value: unknown) => void = () => {};
    finalizeNewModel2.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFinalize = resolve;
        })
    );
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();
    await userEvent.click(screen.getByRole('button', { name: /^finalize$/i }));

    // Confirm modal closes immediately; the full-page overlay takes over.
    expect(await screen.findByText('Finalizing plan…')).toBeInTheDocument();
    expect(screen.queryByText('Finalize this plan?')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /finalizing…/i })).toBeDisabled();

    resolveFinalize({ ok: true, total_committed: 100000, available_funds: 100000, vendor_count: 1 });

    await waitFor(() => expect(screen.queryByText('Finalizing plan…')).not.toBeInTheDocument());
    expect(await screen.findByRole('button', { name: /finalize plan/i })).not.toBeDisabled();
  });
});
