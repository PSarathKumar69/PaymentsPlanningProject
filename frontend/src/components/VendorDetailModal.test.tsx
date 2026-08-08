import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { VendorDetailModal } from './VendorDetailModal';
import { Vendor } from '../types';

const getVendorAging = vi.fn();
const getVendorPayments = vi.fn();
vi.mock('../api/vendors', () => ({
  getVendorAging: (...args: unknown[]) => getVendorAging(...args),
  getVendorPayments: (...args: unknown[]) => getVendorPayments(...args),
}));

const getVendorMinFundsRequired = vi.fn();
vi.mock('../api/newModel2', () => ({
  getVendorMinFundsRequired: (...args: unknown[]) => getVendorMinFundsRequired(...args),
}));

const postVendorTalkingPoints = vi.fn();
vi.mock('../api/aiCompanion', () => ({
  postVendorTalkingPoints: (...args: unknown[]) => postVendorTalkingPoints(...args),
}));

const vendorA: Vendor = {
  id: 1, erp_code: 'V-001', entity: 'E1', vendor_name: 'Acme Traders', opening_balance: 0,
  live_outstanding_balance: 1000, category: 'normal', commitment_months: null, assigned_week: null,
  payment_status: 'not_paid', paid_so_far_this_month: 0, score: null, current_aging_bucket: '31-60',
  reconsider: null, override_amount: null, priority_tag: 'P2',
};

beforeEach(() => {
  getVendorAging.mockReset().mockResolvedValue({
    oldest_tranche_month: null, oldest_bucket: '31-60', oldest_bucket_months_back: 1, bucket_balances: {},
    total_outstanding: 1000, oldest_bucket_amount: 1000, oldest_bucket_amount_opening: 1000,
    monthly_breakdown: [], week_actual_paid: {}, min_funds_required: 1000, min_funds_tranches: [],
  });
  getVendorPayments.mockReset().mockResolvedValue([]);
  getVendorMinFundsRequired.mockReset().mockResolvedValue({
    total: 1000, rule: 'v2_only_current', category: 'normal', current_month: '2026-08', current_amount: 1000,
    oldest_month: null, oldest_amount: null, second_month: null, second_amount: null, opening_balance: 0,
    live_balance: 1000, commitment_months: null, planning_month: '2026-08', as_of: '2026-07-01',
  });
  postVendorTalkingPoints.mockReset().mockResolvedValue({
    vendor_id: 1, erp_code: 'V-001', vendor_name: 'Acme Traders', category: 'normal', priority_tag: 'P2',
    status: 'partial', required_amount: 1000, allocated_amount: 600, cut_from_full: true,
    aging_bucket: '31-60', script_text: 'narrative for Acme',
  });
});

describe('VendorDetailModal — Ask AI link', () => {
  it('opens the consolidated card pre-selected to this vendor, any allocation status', async () => {
    render(
      <VendorDetailModal
        vendor={vendorA}
        showPayment={false}
        planningMonth="2026-08"
        hasGeneratedThisCycle={true}
        effectiveAmount={600}
        onClose={vi.fn()}
        onPaymentLogged={vi.fn()}
        onCommitmentMonthsChange={vi.fn()}
      />
    );
    await userEvent.click(screen.getByRole('button', { name: /ask ai about this vendor/i }));
    await waitFor(() => expect(postVendorTalkingPoints).toHaveBeenCalledWith(1, 'talking'));
    expect(await screen.findByText('narrative for Acme')).toBeInTheDocument();
    // Preselected path skips the vendor picker entirely.
    expect(screen.queryByPlaceholderText(/vendor name or erp code/i)).not.toBeInTheDocument();
  });

  it('does not show the Ask AI link in Pay mode', () => {
    render(
      <VendorDetailModal
        vendor={vendorA}
        showPayment={true}
        planningMonth="2026-08"
        hasGeneratedThisCycle={true}
        effectiveAmount={600}
        onClose={vi.fn()}
        onPaymentLogged={vi.fn()}
        onCommitmentMonthsChange={vi.fn()}
      />
    );
    expect(screen.queryByRole('button', { name: /ask ai about this vendor/i })).not.toBeInTheDocument();
  });
});
