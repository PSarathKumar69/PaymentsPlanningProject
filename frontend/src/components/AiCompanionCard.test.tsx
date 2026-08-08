import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AiCompanionCard } from './AiCompanionCard';
import { Vendor, VendorTalkingPointsResult } from '../types';

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

const result: VendorTalkingPointsResult = {
  vendor_id: 1, erp_code: 'V-001', vendor_name: 'Acme Traders', category: 'normal', priority_tag: 'P2',
  status: 'partial', required_amount: 1000, allocated_amount: 600, cut_from_full: true,
  aging_bucket: '31-60', script_text: 'Explain to Acme Traders that funds are limited this cycle.',
};

const emailResult: VendorTalkingPointsResult = { ...result, script_text: 'Dear Acme Traders team, ...' };

beforeEach(() => {
  postVendorTalkingPoints.mockReset().mockImplementation((_id: number, format: string = 'talking') =>
    Promise.resolve(format === 'email' ? emailResult : result)
  );
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

describe('AiCompanionCard — search entry path', () => {
  it('picking a vendor fetches and shows facts + narrative', async () => {
    render(<AiCompanionCard planVendors={[vendorA]} preselectedVendor={null} onClose={vi.fn()} />);
    await userEvent.type(screen.getByPlaceholderText(/vendor name or erp code/i), 'Acme');
    await userEvent.click(screen.getByRole('button', { name: /Acme Traders \(V-001\)/ }));

    await waitFor(() => expect(postVendorTalkingPoints).toHaveBeenCalledWith(1, 'talking'));
    expect(await screen.findByText(result.script_text)).toBeInTheDocument();
    expect(screen.getByText('₹1,000')).toBeInTheDocument(); // required
    expect(screen.getByText('₹600')).toBeInTheDocument(); // allocated
    expect(screen.getByText('Cut from full')).toBeInTheDocument();
  });

  it('shows nothing to pick when there is no current plan', () => {
    render(<AiCompanionCard planVendors={[]} preselectedVendor={null} onClose={vi.fn()} />);
    expect(screen.getByText(/generate a plan first/i)).toBeInTheDocument();
  });
});

describe('AiCompanionCard — preselected entry path', () => {
  it('skips the picker and fetches immediately, defaulting to the Talking tab', async () => {
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    await waitFor(() => expect(postVendorTalkingPoints).toHaveBeenCalledWith(1, 'talking'));
    expect(await screen.findByText(result.script_text)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/vendor name or erp code/i)).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /talking/i })).toHaveAttribute('aria-selected', 'true');
  });

  it('shows an error state when the call fails', async () => {
    const { ApiError } = await import('../api/client');
    postVendorTalkingPoints.mockRejectedValue(new ApiError('boom'));
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    expect(await screen.findByText(/ERROR: boom/)).toBeInTheDocument();
  });

  it('copy writes the narrative to the clipboard', async () => {
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    await screen.findByText(result.script_text);
    await userEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(result.script_text);
    expect(await screen.findByText('Copied!')).toBeInTheDocument();
  });

  it('regenerate calls the endpoint again for the active tab only', async () => {
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    await screen.findByText(result.script_text);
    await userEvent.click(screen.getByRole('button', { name: /regenerate/i }));
    await waitFor(() => expect(postVendorTalkingPoints).toHaveBeenCalledTimes(2));
    expect(postVendorTalkingPoints).toHaveBeenLastCalledWith(1, 'talking');
  });
});

describe('AiCompanionCard — Talking/Email toggle', () => {
  it('switching to Email lazily fetches only on first visit, then caches', async () => {
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    await screen.findByText(result.script_text);
    expect(postVendorTalkingPoints).toHaveBeenCalledTimes(1); // Talking only so far

    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await waitFor(() => expect(postVendorTalkingPoints).toHaveBeenCalledWith(1, 'email'));
    expect(await screen.findByText(emailResult.script_text)).toBeInTheDocument();
    expect(postVendorTalkingPoints).toHaveBeenCalledTimes(2);

    // Switch back to Talking — already cached, no third call.
    await userEvent.click(screen.getByRole('tab', { name: /talking/i }));
    expect(await screen.findByText(result.script_text)).toBeInTheDocument();
    expect(postVendorTalkingPoints).toHaveBeenCalledTimes(2);

    // Switch to Email again — still cached from before, no fourth call.
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    expect(await screen.findByText(emailResult.script_text)).toBeInTheDocument();
    expect(postVendorTalkingPoints).toHaveBeenCalledTimes(2);
  });

  it('copy and regenerate act on the active tab only', async () => {
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    await screen.findByText(result.script_text);
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await screen.findByText(emailResult.script_text);

    await userEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(emailResult.script_text);

    await userEvent.click(screen.getByRole('button', { name: /regenerate/i }));
    await waitFor(() => expect(postVendorTalkingPoints).toHaveBeenLastCalledWith(1, 'email'));
  });

  it('shows the header/key-facts row immediately when switching to a not-yet-fetched tab, with a loading narrative', async () => {
    let resolveEmail: (v: VendorTalkingPointsResult) => void = () => {};
    postVendorTalkingPoints.mockImplementation((_id: number, format: string = 'talking') => {
      if (format === 'email') return new Promise((resolve) => { resolveEmail = resolve; });
      return Promise.resolve(result);
    });
    render(<AiCompanionCard planVendors={[]} preselectedVendor={vendorA} onClose={vi.fn()} />);
    await screen.findByText(result.script_text);

    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    // Header (shared fact-pack fields) still visible from the Talking fetch.
    expect(screen.getByText(/Acme Traders \(V-001\)/)).toBeInTheDocument();
    // Narrative for Email itself hasn't resolved yet.
    expect(screen.queryByText(emailResult.script_text)).not.toBeInTheDocument();

    resolveEmail(emailResult);
    expect(await screen.findByText(emailResult.script_text)).toBeInTheDocument();
  });
});
