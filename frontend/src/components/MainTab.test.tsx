import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MainTab } from './MainTab';

vi.mock('./MasterDataGrid', () => ({ MasterDataGrid: () => <div data-testid="master-grid-stub" /> }));

const commitUpload = vi.fn();
vi.mock('../api/masterData', () => ({
  commitUpload: (...args: unknown[]) => commitUpload(...args),
  revertUpload: vi.fn(),
}));

const getCurrentPlanningMonth = vi.fn();
const getSuggestedPlanningMonth = vi.fn();
vi.mock('../api/newModel2', () => ({
  getCurrentPlanningMonth: () => getCurrentPlanningMonth(),
  getSuggestedPlanningMonth: () => getSuggestedPlanningMonth(),
}));

const pickFile = async () => {
  const file = new File(['x'], 'vendors.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, file);
  return file;
};

describe('MainTab upload confirm modal', () => {
  beforeEach(() => {
    commitUpload.mockReset().mockResolvedValue({
      vendors_changed: 1, new_vendor_count: 0, vendors_with_changed_ledger_count: 1,
      ai_column_mapping_messages: [], cycle_reset: false, vendors_reset: 0,
    });
    getCurrentPlanningMonth.mockReset().mockResolvedValue({ planning_month: '2026-08' });
    getSuggestedPlanningMonth.mockReset().mockResolvedValue({ last_recorded_month: '2026-06', suggested_planning_month: '2026-07' });
  });

  it('opens the confirm modal on file pick, pre-filled from the current cycle planning month', async () => {
    render(<MainTab />);
    await pickFile();
    expect(await screen.findByText('Confirm upload')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByDisplayValue('2026-08')).toBeInTheDocument());
    expect(commitUpload).not.toHaveBeenCalled();
  });

  it('falls back to the suggested planning month when no cycle value is set yet', async () => {
    getCurrentPlanningMonth.mockResolvedValue({ planning_month: null });
    render(<MainTab />);
    await pickFile();
    await waitFor(() => expect(screen.getByDisplayValue('2026-07')).toBeInTheDocument());
  });

  it('cancel discards the picked file — no request made', async () => {
    render(<MainTab />);
    await pickFile();
    await screen.findByText('Confirm upload');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText('Confirm upload')).not.toBeInTheDocument();
    expect(commitUpload).not.toHaveBeenCalled();
  });

  it('confirm calls commitUpload with the picked file and planning month', async () => {
    render(<MainTab />);
    const file = await pickFile();
    await screen.findByText('Confirm upload');
    await waitFor(() => expect(screen.getByDisplayValue('2026-08')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Confirm & Upload' }));
    await waitFor(() => expect(commitUpload).toHaveBeenCalledWith(file, '2026-08'));
  });

  it('mentions the cycle reset in the toast only when cycle_reset is true', async () => {
    commitUpload.mockResolvedValue({
      vendors_changed: 1, new_vendor_count: 0, vendors_with_changed_ledger_count: 1,
      ai_column_mapping_messages: [], cycle_reset: true, vendors_reset: 42,
    });
    const onNotify = vi.fn();
    render(<MainTab onNotify={onNotify} />);
    await pickFile();
    await screen.findByText('Confirm upload');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm & Upload' }));
    await waitFor(() => expect(onNotify).toHaveBeenCalledWith(
      expect.stringContaining('New payment cycle started — 42 vendor(s) reset for the new month.'),
      'success'
    ));
  });

  it('says nothing about a cycle reset when cycle_reset is false', async () => {
    const onNotify = vi.fn();
    render(<MainTab onNotify={onNotify} />);
    await pickFile();
    await screen.findByText('Confirm upload');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm & Upload' }));
    await waitFor(() => expect(onNotify).toHaveBeenCalled());
    expect(onNotify).not.toHaveBeenCalledWith(expect.stringContaining('New payment cycle started'), expect.anything());
  });
});
