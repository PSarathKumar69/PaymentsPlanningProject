import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlanningView } from './PlanningView';

// Category dropdown fix (Finance's ask — "can't change Commitment vendor to
// Normal, the dropdown doesn't work"). Root cause: handleCategoryChange used
// to fire a SECOND, redundant patchVendor('priority_tag', ...) call after
// the category patch, re-deriving client-side something the backend already
// derives+writes atomically in the same single-field PATCH. If that second
// call ever failed, refreshVendors() never ran, so the row kept showing the
// OLD category. This suite locks in the fix: exactly ONE patchVendor call
// per category change, followed by a refresh either way.
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

// A Commitment-category vendor — Finance's exact reported scenario.
const COMMITMENT_VENDOR = {
  id: 1,
  erp_code: 'V001',
  entity: 'Acme',
  vendor_name: 'Acme Traders',
  opening_balance: 100000,
  live_outstanding_balance: 100000,
  category: 'commitment',
  commitment_months: 6,
  assigned_week: 1,
  payment_status: 'not_paid',
  paid_so_far_this_month: 0,
  score: 50,
  current_aging_bucket: '0-30',
  reconsider: null,
  override_amount: null,
  priority_tag: 'P1',
};

const PRIORITY_BUCKETS = [
  { bucket_key: 'P2', display_label: 'P2', category_name: 'normal', ceiling_pct: 0.5, floor_pct: 0.1, rotation_position: 0, deletable: true },
  { bucket_key: 'P3', display_label: 'P3', category_name: 'normal', ceiling_pct: 0.5, floor_pct: 0.1, rotation_position: 1, deletable: true },
  { bucket_key: 'P4', display_label: 'P4', category_name: 'normal', ceiling_pct: 0.5, floor_pct: 0.1, rotation_position: 2, deletable: true },
  { bucket_key: 'P5', display_label: 'P5', category_name: 'inactive', ceiling_pct: 0.5, floor_pct: 0.1, rotation_position: 3, deletable: true },
];

const setupMocks = () => {
  listVendors.mockResolvedValue([COMMITMENT_VENDOR]);
  getVendorPaymentTracking.mockResolvedValue([]);
  getPlanRuns.mockResolvedValue({ plan_runs: [], vendor_week_distribution_plans: {} });
  getPriorityBuckets.mockResolvedValue(PRIORITY_BUCKETS);
  getAllVendorsAging.mockResolvedValue([]);
  getVendorCategories.mockResolvedValue([
    { value: 'must_pay', label: 'Must pay' },
    { value: 'commitment', label: 'Commitment' },
    { value: 'normal', label: 'Normal' },
    { value: 'inactive', label: 'Inactive' },
  ]);
  getAllVendorMinFundsRequired.mockResolvedValue({ total: 0, breakdown: [] });
  getMinimumFundsRequired.mockResolvedValue({
    total: 100000, breakdown: [], planning_month: '2026-08', as_of: '2026-07-01', planning_month_warning: null,
  });
  getWeeksInMonth.mockResolvedValue({ weeks: 4 });
  getCurrentPlanningMonth.mockResolvedValue({ planning_month: '2026-08' });
};

describe('PlanningView — category dropdown (Commitment -> Normal)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('fires exactly ONE patchVendor call and refreshes so the row shows the new category', async () => {
    patchVendor.mockResolvedValue({
      old_value: 'commitment',
      new_value: 'normal',
      sibling_field: 'priority_tag',
      sibling_old_value: 'P1',
      sibling_new_value: 'P2',
    });
    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');

    const row = screen.getByText('Acme Traders').closest('tr') as HTMLElement;
    const categorySelect = row.querySelector('select') as HTMLSelectElement;
    expect(categorySelect.value).toBe('commitment');

    // Reflects the change post-edit — refreshVendors() must have re-run.
    listVendors.mockResolvedValue([{ ...COMMITMENT_VENDOR, category: 'normal', priority_tag: 'P2' }]);

    await userEvent.selectOptions(categorySelect, 'normal');

    // Exactly one PATCH call — no redundant client-side sibling patch.
    await waitFor(() => expect(patchVendor).toHaveBeenCalledTimes(1));
    expect(patchVendor).toHaveBeenCalledWith(1, 'category', 'normal');

    await waitFor(() => expect(listVendors).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((row.querySelector('select') as HTMLSelectElement).value).toBe('normal'));
  });

  it('surfaces a commitment_months_warning from the response as a toast', async () => {
    const onNotify = vi.fn();
    patchVendor.mockResolvedValue({
      old_value: 'commitment',
      new_value: 'normal',
      commitment_months_warning: 'V001 is now Commitment category, but commitment_months is 1 (unconfirmed default).',
    });
    render(<PlanningView onNotify={onNotify} />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');

    const row = screen.getByText('Acme Traders').closest('tr') as HTMLElement;
    const categorySelect = row.querySelector('select') as HTMLSelectElement;

    await userEvent.selectOptions(categorySelect, 'normal');

    await waitFor(() =>
      expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('unconfirmed default'), 'warning')
    );
  });

  it('does not block the refresh when the single patch call fails — shows the error toast', async () => {
    const onNotify = vi.fn();
    patchVendor.mockRejectedValue(new Error('network blip'));
    render(<PlanningView onNotify={onNotify} />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());
    await screen.findByText('Acme Traders');

    const row = screen.getByText('Acme Traders').closest('tr') as HTMLElement;
    const categorySelect = row.querySelector('select') as HTMLSelectElement;

    await userEvent.selectOptions(categorySelect, 'normal');

    await waitFor(() => expect(onNotify).toHaveBeenCalledWith(expect.any(String), 'error'));
    expect(patchVendor).toHaveBeenCalledTimes(1); // never a second attempt
  });
});
