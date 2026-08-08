import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UploadConfirmModal } from './UploadConfirmModal';

const setup = (overrides: Partial<React.ComponentProps<typeof UploadConfirmModal>> = {}) => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const onPlanningMonthChange = vi.fn();
  render(
    <UploadConfirmModal
      fileName="vendors.xlsx"
      planningMonth="2026-08"
      onPlanningMonthChange={onPlanningMonthChange}
      isUploading={false}
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    />
  );
  return { onConfirm, onCancel, onPlanningMonthChange };
};

describe('UploadConfirmModal', () => {
  it('renders the file name and the pre-filled planning month, nothing else', () => {
    setup();
    expect(screen.getByText('vendors.xlsx')).toBeInTheDocument();
    expect(screen.getByDisplayValue('2026-08')).toBeInTheDocument();
    expect(screen.queryByText(/sheet start month/i)).not.toBeInTheDocument();
  });

  it('cancel calls onCancel and never onConfirm', async () => {
    const { onCancel, onConfirm } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('confirm calls onConfirm with nothing changed by the click itself (values live in the parent)', async () => {
    const { onConfirm } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm & Upload' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('confirm button is disabled when planning month is blank', () => {
    setup({ planningMonth: '' });
    expect(screen.getByRole('button', { name: 'Confirm & Upload' })).toBeDisabled();
  });

  it('shows Uploading state and disables both buttons while in flight', () => {
    setup({ isUploading: true });
    expect(screen.getByRole('button', { name: 'Uploading…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });
});
