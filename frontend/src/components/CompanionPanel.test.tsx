import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CompanionPanel } from './CompanionPanel';
import { Vendor } from '../types';

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
  postVendorTalkingPoints.mockReset().mockResolvedValue({
    vendor_id: 1, erp_code: 'V-001', vendor_name: 'Acme Traders', category: 'normal', priority_tag: 'P2',
    status: 'partial', required_amount: 1000, allocated_amount: 600, cut_from_full: false,
    aging_bucket: '31-60', script_text: 'narrative',
  });
});

describe('CompanionPanel', () => {
  it('is closed by default and opens the consolidated card via the floating button', async () => {
    render(<CompanionPanel planVendors={[vendorA]} />);
    expect(screen.queryByText(/ask ai about a vendor/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /ask ai about a vendor/i }));
    expect(screen.getByText(/which vendor do you want talking points for/i)).toBeInTheDocument();
  });
});
