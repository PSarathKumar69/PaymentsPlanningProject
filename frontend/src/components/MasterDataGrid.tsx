import React, { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { getMasterGrid, patchExtraField } from '../api/masterData';
import { GridColumn, GridVendorRow, MasterGrid } from '../types';
import { ApiError } from '../api/client';
import { formatMoney } from '../utils/format';
import { ToastVariant } from './NotificationToast';

interface MasterDataGridProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
  // Bump this from the parent after a successful upload/revert to force a
  // reload — MainTab owns that event, this component just reacts to it.
  refreshSignal: number;
}

const MONEY_KINDS = new Set([
  'opening_balance', 'payable', 'total_payable', 'payment', 'total_payment', 'closing_balance',
]);

const ADD_NEW_VALUE = '__add_new__';

// Sticky/frozen identity columns (Main-tab polish task) — fixed pixel
// widths so left offsets are predictable even with this grid's ~450
// row x 40+ column real data. erp_code and vendor_name are the only two
// columns ever sticky; every other column scrolls normally.
const STICKY_WIDTH: Record<string, number> = { erp_code: 110, vendor_name: 200 };
const stickyLeftOffset = (kind: string) => (kind === 'vendor_name' ? STICKY_WIDTH.erp_code : 0);

// React port of test_ui.html's loadMasterDataGrid()/masterGridCellHtml()/
// masterExtraFieldWidgetHtml()/handleMasterDropdownChange() (real state,
// not innerHTML strings). Deliberate divergence from that reference: its
// masterGridCellHtml() still has a dead free-text-input branch for
// category/commitment_months/assigned_week (backend/ingestion/grid.py's
// _describe_columns() has always set editable=false for those four columns
// — that editing moved to the Planning tab's inline dropdowns, docs/11 Task
// 2) — not carried forward here. Only `kind === "extra"` is ever editable.
export const MasterDataGrid: React.FC<MasterDataGridProps> = ({ onNotify, refreshSignal }) => {
  const [grid, setGrid] = useState<MasterGrid | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [filterText, setFilterText] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      setGrid(await getMasterGrid());
      setLoadError('');
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  const handleExtraFieldEdit = async (vendorId: number, columnName: string, newValue: string) => {
    try {
      await patchExtraField(vendorId, columnName, newValue);
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    }
    await load(); // re-fetch from source of truth either way — a rejected edit must not look like it stuck
  };

  const handleDropdownChange = (vendorId: number, columnName: string, selected: string) => {
    if (selected !== ADD_NEW_VALUE) {
      handleExtraFieldEdit(vendorId, columnName, selected);
      return;
    }
    const newValue = (window.prompt('New value for this column:') || '').trim();
    if (!newValue) {
      load(); // reset the <select> back to its real current value
      return;
    }
    handleExtraFieldEdit(vendorId, columnName, newValue);
  };

  const duplicateErpCodes = useMemo(() => new Set(grid?.duplicate_erp_codes ?? []), [grid]);

  const filteredVendors = useMemo(() => {
    if (!grid) return [];
    const q = filterText.trim().toLowerCase();
    if (!q) return grid.vendors;
    return grid.vendors.filter((row) => {
      const name = String(row.values['Vendor Name'] ?? row.erp_code ?? '').toLowerCase();
      return row.erp_code.toLowerCase().includes(q) || name.includes(q);
    });
  }, [grid, filterText]);

  const isStickyCol = (col: GridColumn) => STICKY_WIDTH[col.kind] != null;
  const stickyCellStyle = (col: GridColumn): React.CSSProperties =>
    isStickyCol(col) ? { left: stickyLeftOffset(col.kind), width: STICKY_WIDTH[col.kind], minWidth: STICKY_WIDTH[col.kind] } : {};

  // "No data yet" fix — before any vendor has ever been ingested (fresh
  // environment, or DB/master file wiped), show a friendly placeholder in
  // the grid's position instead of an empty table shell. Judged by the
  // backend's own has_data flag (build_master_grid()), not by
  // vendors.length === 0 — that's also the ordinary "every real vendor got
  // filtered/deactivated" shape, which must still render the real
  // (empty-bodied) table, not this placeholder.
  if (!loading && grid && !grid.has_data) {
    return (
      <div className="bg-white border border-gray-200/90 rounded-xl p-10 shadow-xs flex flex-col items-center justify-center gap-1.5 w-full text-center">
        <h3 className="text-sm font-bold text-gray-900">Master data grid</h3>
        <p className="text-xs text-gray-400 max-w-sm">Upload a file to see your master data here.</p>
      </div>
    );
  }

  const renderCell = (col: GridColumn, row: GridVendorRow) => {
    const value = row.values[col.header];

    if (!col.editable) {
      if (value === null || value === undefined) return <span className="text-gray-300">—</span>;
      const display = MONEY_KINDS.has(col.kind) ? formatMoney(value as number) : String(value);
      if (col.kind === 'erp_code' && duplicateErpCodes.has(row.erp_code)) {
        return (
          <span className="inline-flex items-center gap-1">
            {display}
            <span
              className="text-[10px] font-semibold text-red-700 bg-red-100 border border-red-200 rounded px-1 py-0.5 whitespace-nowrap"
              title="This ERP code appears more than once in the uploaded sheet — only this row was kept, see below the grid."
            >
              Duplicate
            </span>
          </span>
        );
      }
      return <>{display}</>;
    }

    // Only ever col.kind === "extra" — grid.py never sets editable=true otherwise.
    const columnName = col.column_name as string;
    const widgetInfo = grid?.extra_field_widgets[columnName] ?? { widget: 'text', options: null };
    const stringValue = value === null || value === undefined ? '' : String(value);
    const cellKey = `${row.vendor_id}-${columnName}-${stringValue}`;
    const selectCls = 'text-xs border border-gray-200 rounded px-1.5 py-1 w-full min-w-24';

    if (widgetInfo.widget === 'toggle' && widgetInfo.options && widgetInfo.options.length === 2) {
      return (
        <select key={cellKey} defaultValue={stringValue} className={selectCls}
          onChange={(e) => handleExtraFieldEdit(row.vendor_id, columnName, e.target.value)}>
          {widgetInfo.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (widgetInfo.widget === 'dropdown') {
      return (
        <select key={cellKey} defaultValue={stringValue} className={selectCls}
          onChange={(e) => handleDropdownChange(row.vendor_id, columnName, e.target.value)}>
          {(widgetInfo.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
          <option value={ADD_NEW_VALUE}>+ Add new value</option>
        </select>
      );
    }
    return (
      <input key={cellKey} type="text" defaultValue={stringValue} className={selectCls}
        onBlur={(e) => handleExtraFieldEdit(row.vendor_id, columnName, e.target.value)} />
    );
  };

  return (
    <div className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-xs flex flex-col gap-3 w-full">
      <div className="flex items-center justify-between pb-2 border-b border-gray-100 gap-3 flex-wrap">
        <h3 className="text-sm font-bold text-gray-900">Master data grid</h3>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter by vendor / ERP code…"
              className="pl-7 pr-3 py-1.5 bg-gray-50/80 border border-gray-200 rounded-lg text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41] w-56"
            />
          </div>
          <button type="button" onClick={load}
            className="text-xs text-gray-500 hover:text-[#107c41] font-medium cursor-pointer whitespace-nowrap">
            Refresh
          </button>
        </div>
      </div>
      {loadError && <p className="text-xs text-red-600">ERROR loading master data grid: {loadError}</p>}
      <div className="overflow-auto thin-scrollbar max-h-[70vh] border border-gray-100 rounded-lg">
        <table className="text-left text-xs border-separate border-spacing-0" style={{ width: 'max-content', minWidth: '100%' }}>
          <thead className="bg-gray-50 text-gray-500 font-semibold">
            <tr>
              {(grid?.columns ?? []).map((c) => (
                <th
                  key={c.header}
                  className={`sticky top-0 bg-gray-50 py-2 px-3 whitespace-nowrap border-b border-gray-200/80 ${isStickyCol(c) ? 'z-3' : 'z-2'}`}
                  style={stickyCellStyle(c)}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={grid?.columns.length || 1} className="py-4 text-center text-gray-400">Loading…</td></tr>
            ) : filteredVendors.length > 0 ? (
              filteredVendors.map((row, i) => {
                const isDuplicate = duplicateErpCodes.has(row.erp_code);
                return (
                  <tr
                    key={row.vendor_id}
                    className={`${isDuplicate ? 'bg-red-50' : i % 2 === 1 ? 'bg-gray-50/40' : 'bg-white'} hover:bg-emerald-50/40`}
                  >
                    {grid!.columns.map((c) => (
                      <td
                        key={c.header}
                        className={`py-2 px-3 whitespace-nowrap border-b border-gray-100 ${isStickyCol(c) ? 'sticky z-1 bg-inherit' : ''}`}
                        style={stickyCellStyle(c)}
                      >
                        {renderCell(c, row)}
                      </td>
                    ))}
                  </tr>
                );
              })
            ) : (
              <tr><td colSpan={grid?.columns.length || 1} className="py-4 text-center text-gray-400">
                {grid && grid.vendors.length > 0 ? 'No vendors match this filter.' : 'No vendors.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      {grid && (grid.duplicate_dropped_rows?.length ?? 0) > 0 && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5 leading-relaxed">
          <p className="font-semibold mb-1.5">
            {grid.duplicate_dropped_rows.length} row{grid.duplicate_dropped_rows.length === 1 ? '' : 's'} from your
            uploaded sheet share an ERP code with another vendor and were NOT loaded into the plan — only the first
            occurrence of each code is used. Check your source Excel.
          </p>
          <div className="overflow-auto max-h-40 border border-red-200/70 rounded">
            <table className="w-full text-left">
              <thead className="bg-red-100/60">
                <tr>
                  <th className="py-1 px-2 font-semibold">ERP Code</th>
                  <th className="py-1 px-2 font-semibold">Vendor Name</th>
                  <th className="py-1 px-2 font-semibold">Excel Row</th>
                </tr>
              </thead>
              <tbody>
                {grid.duplicate_dropped_rows.map((d, i) => (
                  <tr key={`${d.erp_code}-${d.row}-${i}`} className="border-t border-red-200/50">
                    <td className="py-1 px-2">{d.erp_code}</td>
                    <td className="py-1 px-2">{String(d.vendor_name ?? '—')}</td>
                    <td className="py-1 px-2">{d.row}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
