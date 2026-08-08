import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlanningView } from './PlanningView';

// PlanningView pulls in a lot of API modules just to render its four-card
// cycle flow + table — mocked minimally here since this test only cares
// about the Finalize-shortfall warning living inside the Finalize confirm
// modal (Sarath's correction: not a standalone main-screen banner).
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
const resetNewModel2Cycle = vi.fn();
const getCurrentPlanningMonth = vi.fn();
vi.mock('../api/newModel2', () => ({
  getPlanRuns: () => getPlanRuns(),
  getMinimumFundsRequired: (...args: unknown[]) => getMinimumFundsRequired(...args),
  getAllVendorMinFundsRequired: (...args: unknown[]) => getAllVendorMinFundsRequired(...args),
  generatePlanAndWeeklyView: (...args: unknown[]) => generatePlanAndWeeklyView(...args),
  finalizeNewModel2: () => finalizeNewModel2(),
  resetNewModel2Cycle: () => resetNewModel2Cycle(),
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
  opening_balance: 100000,
  live_outstanding_balance: 100000,
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

const PLAN_RUN = {
  plan_run_id: 1,
  created_at: '2026-07-01T00:00:00',
  month: '2026-08-01',
  model_used: 'new_model_2',
  funds_figure: 100000,
  allocations: [
    { plan_allocation_id: 1, vendor_id: 1, assigned_week: 1, within_week_order: 1, allocated_amount: 100000, override_amount: null, required_amount_snapshot: 100000 },
  ],
};

const setupMocks = () => {
  listVendors.mockResolvedValue([VENDOR]);
  getVendorPaymentTracking.mockResolvedValue([]);
  getPlanRuns.mockResolvedValue({ plan_runs: [PLAN_RUN], vendor_week_distribution_plans: {} });
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

const clickModalFinalize = async () => {
  await userEvent.click(screen.getByRole('button', { name: 'Finalize' }));
};

describe('PlanningView — Finalize shortfall preview, never blocks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('shows a proactive "Short by" preview the moment the modal opens (client-computed, before any Finalize click)', async () => {
    // funds_figure (100000) < allocated total (150000) — a real shortfall,
    // computed client-side from state already on hand, independent of
    // whatever finalizeNewModel2() itself later returns.
    getPlanRuns.mockResolvedValue({
      plan_runs: [{
        ...PLAN_RUN,
        allocations: [{ ...PLAN_RUN.allocations[0], allocated_amount: 150000 }],
      }],
      vendor_week_distribution_plans: {},
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();
    expect(await screen.findByText(/short by ₹50,000/i)).toBeInTheDocument();
    expect(screen.getByText(/try reducing allocations for normal\/inactive vendors, or increasing available funds/i)).toBeInTheDocument();

    // Finalize stays a real, clickable action — never disabled by the preview.
    expect(screen.getByRole('button', { name: 'Finalize' })).toBeEnabled();
  });

  it('closes the modal even when the backend reports a shortfall — Finalize never blocks', async () => {
    finalizeNewModel2.mockResolvedValue({
      ok: false, total_committed: 150000, available_funds: 100000, over_by: 50000, responsible_vendors: [], vendor_count: 0,
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();
    await clickModalFinalize();

    await waitFor(() => expect(screen.queryByText('Finalize this plan?')).not.toBeInTheDocument());
    expect(finalizeNewModel2).toHaveBeenCalledTimes(1);
  });

  it('closes the modal with no preview when funds exactly cover the plan', async () => {
    finalizeNewModel2.mockResolvedValue({
      ok: true, total_committed: 100000, available_funds: 100000, over_by: 0, responsible_vendors: [], vendor_count: 1,
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await openFinalizeConfirm();
    expect(screen.queryByText(/short by/i)).not.toBeInTheDocument();

    await clickModalFinalize();
    await waitFor(() => expect(screen.queryByText('Finalize this plan?')).not.toBeInTheDocument());
  });
});

const fundsLeftValueText = () => {
  const card = screen.getByText('Funds Left').closest('.flex.flex-col') as HTMLElement;
  return card.querySelector('span.text-xl.font-bold')?.textContent;
};

describe('PlanningView — Funds Left restored from the plan_run on remount (loadAll), not recomputed', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('reads Funds Left from latestRun.leftover_remaining, ignoring any vendor override_amount', async () => {
    getPlanRuns.mockResolvedValue({
      plan_runs: [{ ...PLAN_RUN, leftover_remaining: 45000 }],
      vendor_week_distribution_plans: {},
    });
    // If the old formula (funds_figure - totalOverridden) were still in
    // play, a 60000 override on a 100000 funds_figure plan_run would show
    // 40000 here instead of the persisted 45000.
    listVendors.mockResolvedValue([{ ...VENDOR, override_amount: 60000 }]);

    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    expect(await screen.findByText('₹45,000')).toBeInTheDocument();
    expect(fundsLeftValueText()).toBe('₹45,000');
  });

  it('shows "—" instead of a fabricated number when leftover_remaining is null on an old plan_run', async () => {
    getPlanRuns.mockResolvedValue({
      plan_runs: [{ ...PLAN_RUN, leftover_remaining: null }],
      vendor_week_distribution_plans: {},
    });

    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await waitFor(() => expect(fundsLeftValueText()).toBe('—'));
  });
});

describe('PlanningView — "Overridden only" filter', () => {
  const VENDOR_2 = { ...VENDOR, id: 2, erp_code: 'V002', vendor_name: 'Beta Supplies' };

  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
    listVendors.mockResolvedValue([VENDOR, VENDOR_2]);
    getPlanRuns.mockResolvedValue({
      plan_runs: [{
        ...PLAN_RUN,
        allocations: [
          { plan_allocation_id: 1, vendor_id: 1, assigned_week: 1, within_week_order: 1, allocated_amount: 100000, override_amount: 75000, required_amount_snapshot: 100000 },
          { plan_allocation_id: 2, vendor_id: 2, assigned_week: 1, within_week_order: 2, allocated_amount: 50000, override_amount: null, required_amount_snapshot: 50000 },
        ],
      }],
      vendor_week_distribution_plans: {},
    });
  });

  const openFilters = async () => userEvent.hover(screen.getByTitle('Filters'));

  it('shows only vendors with a real override when toggled on, restores full list when toggled off', async () => {
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');
    expect(screen.getByText('Beta Supplies')).toBeInTheDocument();

    await openFilters();
    await userEvent.click(await screen.findByRole('button', { name: 'Overridden only' }));

    expect(screen.getByText('Acme Traders')).toBeInTheDocument();
    expect(screen.queryByText('Beta Supplies')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Overridden only' }));
    expect(screen.getByText('Acme Traders')).toBeInTheDocument();
    expect(screen.getByText('Beta Supplies')).toBeInTheDocument();
  });

  it('combines with an existing filter as AND, not OR', async () => {
    listVendors.mockResolvedValue([
      VENDOR, // normal, overridden
      { ...VENDOR_2, category: 'must_pay' }, // must_pay, not overridden
    ]);
    getVendorCategories.mockResolvedValue([
      { value: 'normal', label: 'Normal' },
      { value: 'must_pay', label: 'Must Pay' },
    ]);
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');

    await openFilters();
    // Category = must_pay (only Beta) AND Overridden only (only Acme) -> no rows.
    await userEvent.click(await screen.findByRole('button', { name: /must pay/i }));
    await userEvent.click(screen.getByRole('button', { name: 'Overridden only' }));

    expect(screen.queryByText('Acme Traders')).not.toBeInTheDocument();
    expect(screen.queryByText('Beta Supplies')).not.toBeInTheDocument();
  });
});

describe('PlanningView — Reset button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('is disabled with nothing generated yet, enabled once a plan exists', async () => {
    getPlanRuns.mockResolvedValue({ plan_runs: [], vendor_week_distribution_plans: {} });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    expect(await screen.findByRole('button', { name: /^reset$/i })).toBeDisabled();
  });

  it('opens a confirm modal, and does nothing until confirmed', async () => {
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    const resetButton = await screen.findByRole('button', { name: /^reset$/i });
    expect(resetButton).toBeEnabled();
    await userEvent.click(resetButton);

    expect(await screen.findByText("Reset this cycle's plan?")).toBeInTheDocument();
    expect(resetNewModel2Cycle).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText("Reset this cycle's plan?")).not.toBeInTheDocument();
    expect(resetNewModel2Cycle).not.toHaveBeenCalled();
  });

  it('on confirm, calls the endpoint and reloads to the post-upload state: no Min Funds figure, funds input disabled', async () => {
    resetNewModel2Cycle.mockResolvedValue({
      planning_month: '2026-08', deleted_plan_runs: 1, deleted_allocations: 1, vendors_reset: 1,
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    await userEvent.click(await screen.findByRole('button', { name: /^reset$/i }));
    await screen.findByText("Reset this cycle's plan?");

    // After Reset, GET /models/5/plan-runs comes back empty (loadAll()'s
    // own re-fetch) — same "nothing generated yet" shape as a fresh cycle.
    getPlanRuns.mockResolvedValue({ plan_runs: [], vendor_week_distribution_plans: {} });

    await userEvent.click(screen.getByRole('button', { name: 'Reset Plan' }));

    await waitFor(() => expect(resetNewModel2Cycle).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText("Reset this cycle's plan?")).not.toBeInTheDocument());

    const fundsInput = screen.getByPlaceholderText('calc funds first') as HTMLInputElement;
    expect(fundsInput).toBeDisabled();
    expect(fundsInput.value).toBe('');
    expect(await screen.findByRole('button', { name: /^reset$/i })).toBeDisabled();
  });
});

describe('PlanningView — a custom category added via Configuration (Configuration-tab-rebuild task)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  const openFilters = async () => userEvent.hover(screen.getByTitle('Filters'));

  it('renders in the Category filter chips/select and resolves its own live priority tag — no hardcoded P5 special case', async () => {
    const contractorVendor = { ...VENDOR, category: 'Contractor', priority_tag: null };
    listVendors.mockResolvedValue([contractorVendor]);
    getVendorCategories.mockResolvedValue([
      { value: 'must_pay', label: 'Must pay' },
      { value: 'commitment', label: 'Commitment' },
      { value: 'normal', label: 'Normal' },
      { value: 'inactive', label: 'Inactive' },
      { value: 'Contractor', label: 'Contractor' },
    ]);
    getPriorityBuckets.mockResolvedValue([
      { bucket_key: 'P9', display_label: 'P9', category_name: 'Contractor', ceiling_pct: 0.7, floor_pct: 0.15, rotation_position: 0 },
    ]);

    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');

    // The vendor's own Category <select> shows "Contractor" as a real,
    // selectable option (not just the 4 fixed values).
    expect(screen.getByDisplayValue('Contractor')).toBeInTheDocument();
    // Its V-Priority cell resolved P9 (Contractor's own only bucket) as the
    // default for an untagged vendor — never a hardcoded P2/P5 fallback.
    expect(screen.getByDisplayValue('P9')).toBeInTheDocument();

    await openFilters();
    expect(await screen.findByRole('button', { name: 'Contractor' })).toBeInTheDocument();
  });
});
