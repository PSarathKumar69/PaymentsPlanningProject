import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MasterDataGrid } from './MasterDataGrid';

const getMasterGrid = vi.fn();
vi.mock('../api/masterData', () => ({
  getMasterGrid: () => getMasterGrid(),
  patchExtraField: vi.fn(),
}));

describe('MasterDataGrid — no-data placeholder', () => {
  beforeEach(() => getMasterGrid.mockReset());

  it('shows the placeholder, not a table, when has_data is false', async () => {
    getMasterGrid.mockResolvedValue({ columns: [], vendors: [], extra_field_widgets: {}, has_data: false });
    render(<MasterDataGrid refreshSignal={0} />);
    expect(await screen.findByText('Upload a file to see your master data here.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows the real table once has_data is true', async () => {
    getMasterGrid.mockResolvedValue({
      columns: [{ header: 'ERP Code', kind: 'erp_code', editable: false }],
      vendors: [{ vendor_id: 1, erp_code: 'V001', values: { 'ERP Code': 'V001' } }],
      extra_field_widgets: {},
      has_data: true,
    });
    render(<MasterDataGrid refreshSignal={0} />);
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getByText('V001')).toBeInTheDocument();
    expect(screen.queryByText('Upload a file to see your master data here.')).not.toBeInTheDocument();
  });

  it('re-fetches and shows the real table once refreshSignal bumps after an upload', async () => {
    getMasterGrid.mockResolvedValueOnce({ columns: [], vendors: [], extra_field_widgets: {}, has_data: false });
    const { rerender } = render(<MasterDataGrid refreshSignal={0} />);
    await screen.findByText('Upload a file to see your master data here.');

    getMasterGrid.mockResolvedValueOnce({
      columns: [{ header: 'ERP Code', kind: 'erp_code', editable: false }],
      vendors: [{ vendor_id: 1, erp_code: 'V001', values: { 'ERP Code': 'V001' } }],
      extra_field_widgets: {},
      has_data: true,
    });
    rerender(<MasterDataGrid refreshSignal={1} />);
    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
  });
});

describe('MasterDataGrid — duplicate ERP code flagging', () => {
  beforeEach(() => getMasterGrid.mockReset());

  const baseColumns = [
    { header: 'ERP Code', kind: 'erp_code', editable: false },
    { header: 'Vendor Name', kind: 'vendor_name', editable: false },
  ];

  it('highlights a vendor row and badges its ERP code when its code was duplicated in the sheet', async () => {
    getMasterGrid.mockResolvedValue({
      columns: baseColumns,
      vendors: [{ vendor_id: 1, erp_code: 'VT0001', values: { 'ERP Code': 'VT0001', 'Vendor Name': 'Bravo Industrial Supplies' } }],
      extra_field_widgets: {},
      has_data: true,
      duplicate_erp_codes: ['VT0001'],
      duplicate_dropped_rows: [{ erp_code: 'VT0001', vendor_name: 'Alpha Freight Services', row: 2 }],
    });
    render(<MasterDataGrid refreshSignal={0} />);

    expect(await screen.findByText('Duplicate')).toBeInTheDocument();
    const row = screen.getByText('Bravo Industrial Supplies').closest('tr');
    expect(row).toHaveClass('bg-red-50');
  });

  it('renders the dropped-rows section with the sheet row Finance should check', async () => {
    getMasterGrid.mockResolvedValue({
      columns: baseColumns,
      vendors: [{ vendor_id: 1, erp_code: 'VT0001', values: { 'ERP Code': 'VT0001', 'Vendor Name': 'Bravo Industrial Supplies' } }],
      extra_field_widgets: {},
      has_data: true,
      duplicate_erp_codes: ['VT0001'],
      duplicate_dropped_rows: [{ erp_code: 'VT0001', vendor_name: 'Alpha Freight Services', row: 2 }],
    });
    render(<MasterDataGrid refreshSignal={0} />);

    expect(await screen.findByText('Alpha Freight Services')).toBeInTheDocument();
    expect(screen.getByText(/were NOT loaded into the plan/)).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('shows nothing extra when there are no duplicates', async () => {
    getMasterGrid.mockResolvedValue({
      columns: baseColumns,
      vendors: [{ vendor_id: 1, erp_code: 'V001', values: { 'ERP Code': 'V001', 'Vendor Name': 'Solo Vendor' } }],
      extra_field_widgets: {},
      has_data: true,
      duplicate_erp_codes: [],
      duplicate_dropped_rows: [],
    });
    render(<MasterDataGrid refreshSignal={0} />);

    await screen.findByText('Solo Vendor');
    expect(screen.queryByText('Duplicate')).not.toBeInTheDocument();
    expect(screen.queryByText(/were NOT loaded into the plan/)).not.toBeInTheDocument();
  });
});
