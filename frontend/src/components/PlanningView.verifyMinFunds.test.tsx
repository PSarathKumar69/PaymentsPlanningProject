import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlanningView } from './PlanningView';

// "Verify Min Funds" download button — separate file from PlanningView.test.tsx
// so this suite's api/planExport mock (unused/unmocked there) doesn't have to
// be threaded through every existing test in that file.
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

const downloadMinFundsVerificationExport = vi.fn();
vi.mock('../api/planExport', () => ({
  downloadFinalizedPlanExport: vi.fn(),
  downloadMinFundsVerificationExport: () => downloadMinFundsVerificationExport(),
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

const setupMocks = () => {
  listVendors.mockResolvedValue([VENDOR]);
  getVendorPaymentTracking.mockResolvedValue([]);
  getPlanRuns.mockResolvedValue({ plan_runs: [], vendor_week_distribution_plans: {} });
  getPriorityBuckets.mockResolvedValue([]);
  getAllVendorsAging.mockResolvedValue([]);
  getVendorCategories.mockResolvedValue([{ value: 'normal', label: 'Normal' }]);
  getAllVendorMinFundsRequired.mockResolvedValue({ total: 0, breakdown: [] });
  getMinimumFundsRequired.mockResolvedValue({
    total: 100000, breakdown: [], planning_month: '2026-08', as_of: '2026-07-01', planning_month_warning: null,
  });
  getWeeksInMonth.mockResolvedValue({ weeks: 4 });
  getCurrentPlanningMonth.mockResolvedValue({ planning_month: '2026-08' });
};

describe('PlanningView — Verify Min Funds download button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:mock'), revokeObjectURL: vi.fn() });
  });

  it('is disabled until Minimum Funds Required has been calculated, then enabled and triggers the download', async () => {
    generatePlanAndWeeklyView.mockResolvedValue({ plan: { allocations: [], leftover_remaining: 0 } });
    downloadMinFundsVerificationExport.mockResolvedValue({ blob: new Blob(['x']), filename: 'Min Funds Verification - Aug-2026.xlsx' });

    render(<PlanningView />);
    await waitFor(() => expect(listVendors).toHaveBeenCalled());

    const verifyButton = await screen.findByRole('button', { name: /verify min funds/i });
    expect(verifyButton).toBeDisabled();

    // Generate Plan is what refreshes nm2MinFundsByVendorId off a non-empty
    // breakdown (PlanningView.tsx's refreshNm2MinFunds()) — but Generate
    // Plan itself is disabled (CLAUDE.md rule 4) until Cal Min Funds unlocks
    // the funds input first.
    await userEvent.click(screen.getByRole('button', { name: /cal min funds/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: /generate plan/i })).toBeEnabled());

    getAllVendorMinFundsRequired.mockResolvedValue({
      total: 5000, breakdown: [{ vendor_id: 1, erp_code: 'V001', vendor_name: 'Acme Traders', category: 'normal', required_amount: 5000, rule: 'oldest' }],
    });
    await userEvent.click(screen.getByRole('button', { name: /generate plan/i }));

    await waitFor(() => expect(verifyButton).toBeEnabled());

    await userEvent.click(verifyButton);
    await waitFor(() => expect(downloadMinFundsVerificationExport).toHaveBeenCalledTimes(1));
  });
});
