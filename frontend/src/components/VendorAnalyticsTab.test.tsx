import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VendorAnalyticsTab from './VendorAnalyticsTab';

const getAnalyticsDashboard = vi.fn();
const getAnalyticsFundsTrend = vi.fn();
const downloadAnalyticsExport = vi.fn();
vi.mock('../api/analytics', () => ({
  getAnalyticsDashboard: (...args: unknown[]) => getAnalyticsDashboard(...args),
  getAnalyticsFundsTrend: (...args: unknown[]) => getAnalyticsFundsTrend(...args),
  downloadAnalyticsExport: (...args: unknown[]) => downloadAnalyticsExport(...args),
}));

// Only 3 real months — confirms the table/charts iterate whatever the API
// actually returns instead of a fixed 14-month mock constant.
const THREE_MONTHS = ['2026-01-01', '2026-02-01', '2026-03-01'];

const dashboardFixture = {
  vendors: [
    {
      vendor_id: 1,
      erp_code: 'V-001',
      vendor_name: 'Acme Traders',
      outstanding_balance: 5000,
      aging_buckets: { '0-30': 1000, '31-60': 4000, '61-90': 0, '91-120': 0, '120+': 0 },
      oldest_bucket: '31-60',
      oldest_bucket_months_back: 1,
      oldest_bucket_amount: 4000,
      history: THREE_MONTHS.map((month, i) => ({ month, payable: 1000 * (i + 1), payment: 500 * (i + 1) })),
      monthly_breakdown: THREE_MONTHS.map((month, i) => ({
        months_back: THREE_MONTHS.length - 1 - i,
        month,
        label: month,
        amount: 111 * (i + 1),
        payable: 222 * (i + 1),
        payment: 333 * (i + 1),
      })),
    },
    {
      vendor_id: 2,
      erp_code: 'V-002',
      vendor_name: 'Beta Corp',
      outstanding_balance: 1000,
      aging_buckets: { '0-30': 1000, '31-60': 0, '61-90': 0, '91-120': 0, '120+': 0 },
      oldest_bucket: '0-30',
      oldest_bucket_months_back: 0,
      oldest_bucket_amount: 1000,
      history: THREE_MONTHS.map((month, i) => ({ month, payable: 500 * (i + 1), payment: 500 * (i + 1) })),
      monthly_breakdown: [],
    },
  ],
  months: THREE_MONTHS,
  aggregates: THREE_MONTHS.map((month, i) => ({
    month,
    total_payable: 1000 * (i + 1),
    total_payment: 500 * (i + 1),
    cumulative_debt: 500 * (i + 1),
    cumulative_paid: 500 * (i + 1),
  })),
  aging_totals: { '0-30': 1000, '31-60': 4000, '61-90': 0, '91-120': 0, '120+': 0 },
};

const fundsTrendFixture = {
  trend: [{ month: '2026-03-01', available_funds: 800, min_funds_required: 1200 }],
};

beforeEach(() => {
  getAnalyticsDashboard.mockReset().mockResolvedValue(dashboardFixture);
  getAnalyticsFundsTrend.mockReset().mockResolvedValue(fundsTrendFixture);
  downloadAnalyticsExport.mockReset().mockResolvedValue({ blob: new Blob(['x']), filename: 'Vendor Analytics.xlsx' });
  // jsdom has no createObjectURL/revokeObjectURL — stub for the download handler.
  URL.createObjectURL = vi.fn(() => 'blob:mock');
  URL.revokeObjectURL = vi.fn();
});

describe('VendorAnalyticsTab', () => {
  it('renders real fetched data instead of the old mock generator', async () => {
    render(<VendorAnalyticsTab />);
    expect(await screen.findByText('Acme Traders')).toBeInTheDocument();
    expect(getAnalyticsDashboard).toHaveBeenCalledTimes(1);
    expect(getAnalyticsFundsTrend).toHaveBeenCalledTimes(1);
  });

  it('renders exactly the dynamic month count the API returns, not a fixed 14', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.getByText('Jan 2026')).toBeInTheDocument();
    expect(screen.getByText('Feb 2026')).toBeInTheDocument();
    expect(screen.getByText('Mar 2026')).toBeInTheDocument();
    expect(screen.queryByText('Apr 2026')).not.toBeInTheDocument();
  });

  it('does not render a Liquidity Health badge', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.queryByText(/liquidity health/i)).not.toBeInTheDocument();
  });

  it('Download Excel triggers a real export request', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    await userEvent.click(screen.getByRole('button', { name: /download excel/i }));
    await waitFor(() => expect(downloadAnalyticsExport).toHaveBeenCalledTimes(1));
  });

  it('does not render a Ledger entry count or Refresh Data button (decluttered header)', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.queryByText(/ledger/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /refresh data/i })).not.toBeInTheDocument();
  });

  it('re-fetches once refreshSignal bumps after an upload', async () => {
    const { rerender } = render(<VendorAnalyticsTab refreshSignal={0} />);
    await screen.findByText('Acme Traders');
    expect(getAnalyticsDashboard).toHaveBeenCalledTimes(1);

    rerender(<VendorAnalyticsTab refreshSignal={1} />);
    await waitFor(() => expect(getAnalyticsDashboard).toHaveBeenCalledTimes(2));
    expect(getAnalyticsFundsTrend).toHaveBeenCalledTimes(2);
  });

  it('clicking an Aging bucket filters the vendor table to that bucket, click again clears it', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.getByText('Beta Corp')).toBeInTheDocument();

    await userEvent.click(screen.getByTitle('Show vendors in the 0-30 bucket'));
    expect(screen.getByText('Beta Corp')).toBeInTheDocument();
    expect(screen.queryByText('Acme Traders')).not.toBeInTheDocument();
    expect(screen.getByText('Bucket: 0-30')).toBeInTheDocument();

    await userEvent.click(screen.getByTitle('Show vendors in the 0-30 bucket'));
    expect(screen.getByText('Acme Traders')).toBeInTheDocument();
    expect(screen.getByText('Beta Corp')).toBeInTheDocument();
    expect(screen.queryByText('Bucket: 0-30')).not.toBeInTheDocument();
  });

  it('does not render an "Oldest bucket concentration" line on the Aging card', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.queryByText(/oldest bucket concentration/i)).not.toBeInTheDocument();
  });

  it('opening a vendor detail shows its aging buckets side by side with amounts', async () => {
    render(<VendorAnalyticsTab />);
    await userEvent.click(await screen.findByText('Acme Traders'));

    await screen.findByText('Monthly Breakdown');
    const modal = document.querySelector('.fixed.inset-0') as HTMLElement;
    expect(within(modal).getByText('0-30')).toBeInTheDocument();
    expect(within(modal).getByText('31-60')).toBeInTheDocument();
    expect(within(modal).getByText('₹1,000')).toBeInTheDocument(); // Acme's 0-30 aging_buckets amount
    expect(within(modal).getByText('₹4,000')).toBeInTheDocument(); // Acme's 31-60 aging_buckets amount
  });

  it('vendors table shows all 5 aging bucket columns with amounts, scrollable alongside the month columns', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');

    const table = screen.getByText('Acme Traders').closest('table') as HTMLElement;
    expect(within(table).getByText('Aging')).toBeInTheDocument(); // group header
    expect(within(table).getByText('0-30')).toBeInTheDocument();
    expect(within(table).getByText('31-60')).toBeInTheDocument();
    expect(within(table).getByText('61-90')).toBeInTheDocument();
    expect(within(table).getByText('91-120')).toBeInTheDocument();
    expect(within(table).getByText('120+')).toBeInTheDocument();

    const acmeRow = screen.getByText('Acme Traders').closest('tr') as HTMLElement;
    const cells = within(acmeRow).getAllByRole('cell');
    const agingCells = cells.slice(-5).map((c) => c.textContent);
    expect(agingCells).toEqual(['1,000', '4,000', '0', '0', '0']); // 0-30, 31-60, 61-90, 91-120, 120+
  });

  it('bucket bars have zero height when that bucket has no real balance', async () => {
    getAnalyticsDashboard.mockReset().mockResolvedValue({
      ...dashboardFixture,
      aging_totals: { '0-30': 0, '31-60': 0, '61-90': 0, '91-120': 0, '120+': 0 },
    });
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.getByTitle('Show vendors in the 0-30 bucket')).toHaveStyle({ height: '0%' });
    expect(screen.getByTitle('Show vendors in the 120+ bucket')).toHaveStyle({ height: '0%' });
  });
});
