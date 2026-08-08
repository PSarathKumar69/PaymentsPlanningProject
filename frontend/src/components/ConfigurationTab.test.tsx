import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigurationTab } from './ConfigurationTab';

const getPriorityBuckets = vi.fn();
const addPriorityBucket = vi.fn();
const updatePriorityBucket = vi.fn();
const removePriorityBucket = vi.fn();
const reorderPriorityBuckets = vi.fn();
vi.mock('../api/configuration', () => ({
  getPriorityBuckets: () => getPriorityBuckets(),
  addPriorityBucket: (...args: unknown[]) => addPriorityBucket(...args),
  updatePriorityBucket: (...args: unknown[]) => updatePriorityBucket(...args),
  removePriorityBucket: (...args: unknown[]) => removePriorityBucket(...args),
  reorderPriorityBuckets: (...args: unknown[]) => reorderPriorityBuckets(...args),
}));

const getAuditLog = vi.fn();
vi.mock('../api/auditLog', () => ({
  getAuditLog: (query: unknown) => getAuditLog(query),
}));

const baseEntry = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  timestamp: '2026-08-03T14:03:00',
  vendor_id: 42,
  vendor_name: 'Acme Traders',
  erp_code: 'V00042',
  field_name: 'category',
  old_value: 'Normal',
  new_value: 'Must Pay',
  source: 'excel_upload',
  changed_by: null,
  ...overrides,
});

describe('ConfigurationTab — audit log', () => {
  beforeEach(() => {
    getPriorityBuckets.mockReset().mockResolvedValue([]);
    addPriorityBucket.mockReset().mockResolvedValue({});
    updatePriorityBucket.mockReset().mockResolvedValue({ changed: true });
    removePriorityBucket.mockReset().mockResolvedValue({ removed: 'P2' });
    reorderPriorityBuckets.mockReset().mockResolvedValue({ changed: true });
    getAuditLog.mockReset().mockResolvedValue({ items: [], total: 0 });
  });

  it('shows vendor name + ERP code, not a bare vendor_id', async () => {
    getAuditLog.mockResolvedValue({ items: [baseEntry()], total: 1 });
    render(<ConfigurationTab />);
    expect(await screen.findByText('Acme Traders (V00042)')).toBeInTheDocument();
    expect(screen.queryByText('42')).not.toBeInTheDocument();
  });

  it('shows "System" for a system-level entry with no vendor_id', async () => {
    getAuditLog.mockResolvedValue({
      items: [baseEntry({ vendor_id: null, vendor_name: null, erp_code: null, field_name: 'priority_bucket.P6' })],
      total: 1,
    });
    render(<ConfigurationTab />);
    expect(await screen.findByText('System')).toBeInTheDocument();
    expect(screen.getByText('Priority Bucket P6 Settings')).toBeInTheDocument();
  });

  it('translates field_name and source into plain-language labels', async () => {
    getAuditLog.mockResolvedValue({ items: [baseEntry()], total: 1 });
    render(<ConfigurationTab />);
    expect(await screen.findByText('Category')).toBeInTheDocument();
    // "Excel re-upload" also appears as a <select> option in the source
    // filter — assert at least the row's own badge renders it too.
    expect(screen.getAllByText('Excel re-upload').length).toBeGreaterThan(1);
  });

  it('formats a money field with formatMoney, not the raw stored string', async () => {
    getAuditLog.mockResolvedValue({
      items: [baseEntry({ field_name: 'vendor.override_amount', old_value: '10000', new_value: '15000.5' })],
      total: 1,
    });
    render(<ConfigurationTab />);
    expect(await screen.findByText('Override Amount')).toBeInTheDocument();
    expect(screen.getByText('₹10,000 -> ₹15,001')).toBeInTheDocument();
  });

  it('keeps the one-line plain-English summary as a hover tooltip, not its own column', async () => {
    // The Summary column itself was removed (Sarath's call — a restated
    // prose sentence of the same four columns was the real reason every
    // row grew tall) — the summary still exists, just as the row's title
    // attribute for anyone who wants the full sentence on hover.
    getAuditLog.mockResolvedValue({ items: [baseEntry()], total: 1 });
    render(<ConfigurationTab />);
    const vendorCell = await screen.findByText('Acme Traders (V00042)');
    expect(vendorCell.closest('tr')).toHaveAttribute(
      'title',
      expect.stringContaining("Acme Traders's Category changed from Normal to Must Pay (Excel re-upload).")
    );
  });

  it('passes the search box value to getAuditLog (debounced)', async () => {
    render(<ConfigurationTab />);
    await waitFor(() => expect(getAuditLog).toHaveBeenCalled());
    getAuditLog.mockClear();

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter by vendor / ERP code…'), 'Acme');

    await waitFor(() => expect(getAuditLog).toHaveBeenCalledWith(expect.objectContaining({ search: 'Acme' })), { timeout: 1000 });
  });

  it('shows a Load more button when more rows exist than are loaded, and pages on click', async () => {
    getAuditLog.mockResolvedValue({ items: [baseEntry()], total: 2 });
    render(<ConfigurationTab />);
    const loadMore = await screen.findByText('Load more');

    getAuditLog.mockResolvedValueOnce({ items: [baseEntry({ id: 2 })], total: 2 });
    await userEvent.click(loadMore);

    await waitFor(() => expect(getAuditLog).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 })));
  });
});

const bucket = (overrides: Record<string, unknown> = {}) => ({
  bucket_key: 'P2',
  display_label: 'P2',
  category_name: 'Normal',
  ceiling_pct: 0.95,
  floor_pct: 0.25,
  rotation_position: 0,
  deletable: true,
  ...overrides,
});

describe('ConfigurationTab — categories & priority tags table', () => {
  beforeEach(() => {
    getPriorityBuckets.mockReset();
    addPriorityBucket.mockReset().mockResolvedValue({});
    updatePriorityBucket.mockReset().mockResolvedValue({ changed: true });
    removePriorityBucket.mockReset().mockResolvedValue({ removed: 'P2' });
    reorderPriorityBuckets.mockReset().mockResolvedValue({ changed: true });
    getAuditLog.mockReset().mockResolvedValue({ items: [], total: 0 });
  });

  it('renders P0/P1 as ordinary editable rows — drag handle, identity inputs, and Remove button all present', async () => {
    getPriorityBuckets.mockResolvedValue([
      bucket({ bucket_key: 'P0', category_name: 'Must Pay', ceiling_pct: 1.0, floor_pct: 1.0, rotation_position: -2 }),
      bucket({ bucket_key: 'P1', category_name: 'Commitment', ceiling_pct: 1.0, floor_pct: 1.0, rotation_position: -1 }),
      bucket(),
    ]);
    render(<ConfigurationTab />);
    await screen.findByDisplayValue('P2');

    // Editable as free text now, same as every other row.
    expect(screen.getByDisplayValue('Must Pay')).toBeInTheDocument();
    expect(screen.getByDisplayValue('P0')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Commitment')).toBeInTheDocument();
    expect(screen.getByDisplayValue('P1')).toBeInTheDocument();
    expect(screen.getAllByDisplayValue('100.00')).toHaveLength(4); // P0 ceiling+floor, P1 ceiling+floor

    const rows = screen.getAllByRole('row');
    const p0Row = rows.find((r) => within(r).queryByDisplayValue('P0'));
    expect(p0Row).toBeTruthy();
    expect(p0Row).toHaveAttribute('draggable', 'true');
    expect(within(p0Row!).queryByText('Remove')).toBeInTheDocument();
  });

  it('renders each live category/tag row with its ceiling and floor', async () => {
    getPriorityBuckets.mockResolvedValue([
      bucket({ bucket_key: 'P2', category_name: 'Normal', ceiling_pct: 0.95, floor_pct: 0.25 }),
      bucket({ bucket_key: 'P5', category_name: 'Inactive', ceiling_pct: 0.5, floor_pct: 0.1, rotation_position: 1 }),
    ]);
    render(<ConfigurationTab />);
    expect(await screen.findByDisplayValue('Normal')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Inactive')).toBeInTheDocument();
    expect(screen.getByDisplayValue('95.00')).toBeInTheDocument();
    expect(screen.getByDisplayValue('10.00')).toBeInTheDocument();
  });

  it('adds a brand-new category with an auto-suggested next priority tag', async () => {
    getPriorityBuckets.mockResolvedValue([bucket({ bucket_key: 'P5', rotation_position: 1 })]);
    render(<ConfigurationTab />);
    await screen.findByDisplayValue('Normal');

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Category name (e.g. Contractor)'), 'Contractor');
    await user.type(screen.getByPlaceholderText('Ceiling %'), '70');
    await user.type(screen.getByPlaceholderText('Floor %'), '15');
    await user.click(screen.getByText('+ Add category'));

    await waitFor(() =>
      expect(addPriorityBucket).toHaveBeenCalledWith(
        expect.objectContaining({
          category_name: 'Contractor',
          bucket_key: 'P6', // one past the highest existing numeric tag (P5)
          ceiling_pct: 0.7,
          floor_pct: 0.15,
          rotation_position: 1, // appended at the bottom — cut-first by default
        })
      )
    );
  });

  it('drag-reordering two rows calls the reorder endpoint with the new order', async () => {
    getPriorityBuckets.mockResolvedValue([bucket({ bucket_key: 'P2' }), bucket({ bucket_key: 'P3', rotation_position: 1 })]);
    render(<ConfigurationTab />);
    await screen.findByDisplayValue('P3'); // wait for the real rows, not the transient "Loading…" row
    const rows = screen.getAllByRole('row');
    const p2Row = rows.find((r) => within(r).queryByDisplayValue('P2'));
    const p3Row = rows.find((r) => within(r).queryByDisplayValue('P3'));
    expect(p2Row).toBeTruthy();
    expect(p3Row).toBeTruthy();

    const dataTransfer = { effectAllowed: '', dropEffect: '' };
    fireEvent.dragStart(p2Row!, { dataTransfer });
    fireEvent.dragOver(p3Row!, { dataTransfer });
    fireEvent.drop(p3Row!, { dataTransfer });

    await waitFor(() => expect(reorderPriorityBuckets).toHaveBeenCalledWith(['P3', 'P2']));
  });

  it('editing category name or priority tag calls updatePriorityBucket', async () => {
    getPriorityBuckets.mockResolvedValue([bucket()]);
    render(<ConfigurationTab />);
    const nameInput = await screen.findByDisplayValue('Normal');

    const user = userEvent.setup();
    await user.clear(nameInput);
    await user.type(nameInput, 'Priority Normal');
    await user.tab();

    await waitFor(() =>
      expect(updatePriorityBucket).toHaveBeenCalledWith('P2', { category_name: 'Priority Normal' })
    );
  });

  it("removing a row surfaces the backend's own blocking-vendor error verbatim via onNotify", async () => {
    getPriorityBuckets.mockResolvedValue([bucket()]);
    removePriorityBucket.mockRejectedValue(new Error("cannot remove bucket 'P2' — vendor V00042 is tagged into it"));
    const onNotify = vi.fn();
    render(<ConfigurationTab onNotify={onNotify} />);
    await screen.findByDisplayValue('Normal');
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    await userEvent.click(screen.getByText('Remove'));

    await waitFor(() =>
      expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('vendor V00042 is tagged into it'), 'error')
    );
  });

  it('a pinned (non-deletable) row shows a disabled Remove button and never calls the API', async () => {
    getPriorityBuckets.mockResolvedValue([bucket({ bucket_key: 'P0', category_name: 'Must Pay', deletable: false })]);
    render(<ConfigurationTab />);
    await screen.findByDisplayValue('Must Pay');

    const removeButton = screen.getByText('Remove').closest('button')!;
    expect(removeButton).toBeDisabled();

    await userEvent.click(removeButton);
    expect(removePriorityBucket).not.toHaveBeenCalled();
  });
});
