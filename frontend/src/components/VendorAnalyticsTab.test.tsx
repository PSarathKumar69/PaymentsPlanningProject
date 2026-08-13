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
      // Oldest -> newest (months_back descending to 0), real day-range
      // labels — same shape backend/shared/aging.py's compute_vendor_aging
      // actually returns, not a placeholder.
      monthly_breakdown: THREE_MONTHS.map((month, i) => {
        const monthsBack = THREE_MONTHS.length - 1 - i; // 2, 1, 0
        const label = monthsBack === 0 ? '0-30' : monthsBack === 1 ? '31-60' : '61-90';
        return { months_back: monthsBack, month, label, amount: 1000 * (i + 1), payable: 2000 * (i + 1), payment: 3000 * (i + 1) };
      }),
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
  total_outstanding: 6500,
  outstanding_by_category: { must_pay: 2000, commitment: 1500, normal: 2500, inactive: 500 },
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

  it('renders Total Outstanding + Outstanding by Category cards, not Overall Debt/Overall Paid to Date', async () => {
    render(<VendorAnalyticsTab />);
    await screen.findByText('Acme Traders');
    expect(screen.queryByText('Overall Debt')).not.toBeInTheDocument();
    expect(screen.queryByText('Overall Paid to Date')).not.toBeInTheDocument();

    expect(screen.getByText('Total Outstanding')).toBeInTheDocument();
    expect(screen.getByText('₹6,500')).toBeInTheDocument(); // dashboardFixture.total_outstanding

    expect(screen.getByText('Outstanding by Category')).toBeInTheDocument();
    expect(screen.getByText('Must pay')).toBeInTheDocument();
    expect(screen.getByText('₹2,000')).toBeInTheDocument(); // must_pay
    expect(screen.getByText('Commitment')).toBeInTheDocument();
    expect(screen.getByText('₹1,500')).toBeInTheDocument(); // commitment
    expect(screen.getByText('Normal')).toBeInTheDocument(); // normal — P2/P3/P4 summed as one row
    expect(screen.getByText('₹2,500')).toBeInTheDocument(); // normal
    expect(screen.getByText('Inactive')).toBeInTheDocument();
    expect(screen.getByText('₹500')).toBeInTheDocument(); // inactive
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

  it('opening a vendor detail shows EVERY real aging bucket (not a fixed 5), with amounts', async () => {
    // Real bug (this task): the modal's Aging row used to be hardcoded to
    // exactly 5 boxes (0-30/31-60/61-90/91-120/120+) sourced from the
    // coarse aging_buckets summary — so anything 4+ months back was
    // silently lumped into one "120+" box, which also had an invisible
    // label (label text color == box background color). It's now sourced
    // from monthly_breakdown, one box per real bucket, newest first.
    render(<VendorAnalyticsTab />);
    await userEvent.click(await screen.findByText('Acme Traders'));

    await screen.findByText('Monthly Breakdown');
    const modal = document.querySelector('.fixed.inset-0') as HTMLElement;
    // Scoped to the Aging row itself, not the whole modal — the Monthly
    // Breakdown table below shows the SAME row.amount values (just via
    // formatINR instead of formatINRAbbr, which render identically under
    // ₹1 lakh), so an unscoped query would match twice.
    const agingRow = within(modal).getByText('0-30').closest('.flex-wrap') as HTMLElement;
    expect(within(agingRow).getByText('31-60')).toBeInTheDocument();
    expect(within(agingRow).getByText('61-90')).toBeInTheDocument(); // the old row would never show this — always capped at 5 fixed labels
    expect(within(agingRow).getByText('₹3,000')).toBeInTheDocument(); // newest (0-30)
    expect(within(agingRow).getByText('₹2,000')).toBeInTheDocument(); // 31-60
    expect(within(agingRow).getByText('₹1,000')).toBeInTheDocument(); // oldest (61-90)
  });

  it('opening a vendor detail with more than 5 real months of aging shows all of them, oldest last', async () => {
    const manyMonths = Array.from({ length: 7 }, (_, i) => `2026-0${(i % 9) + 1}-01`);
    getAnalyticsDashboard.mockReset().mockResolvedValue({
      ...dashboardFixture,
      vendors: [
        {
          ...dashboardFixture.vendors[0],
          monthly_breakdown: manyMonths.map((month, i) => {
            const monthsBack = manyMonths.length - 1 - i; // 6..0 — well past the old 5-bucket cap
            const label = monthsBack === 0 ? '0-30' : `${monthsBack * 30 + 1}-${(monthsBack + 1) * 30}`;
            return { months_back: monthsBack, month, label, amount: 100 * (i + 1), payable: 0, payment: 0 };
          }),
        },
        dashboardFixture.vendors[1],
      ],
    });
    render(<VendorAnalyticsTab />);
    await userEvent.click(await screen.findByText('Acme Traders'));

    await screen.findByText('Monthly Breakdown');
    const modal = document.querySelector('.fixed.inset-0') as HTMLElement;
    // 7 real buckets, well past the old fixed 5 — every one of them present.
    expect(within(modal).getByText('0-30')).toBeInTheDocument();
    expect(within(modal).getByText('31-60')).toBeInTheDocument();
    expect(within(modal).getByText('61-90')).toBeInTheDocument();
    expect(within(modal).getByText('91-120')).toBeInTheDocument();
    expect(within(modal).getByText('121-150')).toBeInTheDocument();
    expect(within(modal).getByText('151-180')).toBeInTheDocument();
    expect(within(modal).getByText('181-210')).toBeInTheDocument(); // the oldest bucket — never shown by the old fixed row
  });

  it('opening a vendor detail with no outstanding balance shows a plain message, not an empty row', async () => {
    render(<VendorAnalyticsTab />);
    await userEvent.click(await screen.findByText('Beta Corp'));

    await screen.findByText('Monthly Breakdown');
    const modal = document.querySelector('.fixed.inset-0') as HTMLElement;
    expect(within(modal).getAllByText('No outstanding balance.').length).toBeGreaterThanOrEqual(1);
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
