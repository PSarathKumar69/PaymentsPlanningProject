import React, { useEffect, useState } from 'react';
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

  const renderCell = (col: GridColumn, row: GridVendorRow) => {
    const value = row.values[col.header];

    if (!col.editable) {
      if (value === null || value === undefined) return <span className="text-gray-300">—</span>;
      return <>{MONEY_KINDS.has(col.kind) ? formatMoney(value as number) : String(value)}</>;
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
      <div className="flex items-center justify-between pb-2 border-b border-gray-100">
        <h3 className="text-sm font-bold text-gray-900">Master data grid</h3>
        <button type="button" onClick={load}
          className="text-xs text-gray-500 hover:text-[#107c41] font-medium cursor-pointer">
          Refresh
        </button>
      </div>
      {loadError && <p className="text-xs text-red-600">ERROR loading master data grid: {loadError}</p>}
      <div className="overflow-x-auto no-scrollbar">
        <table className="w-full text-left text-xs border-separate border-spacing-0">
          <thead className="bg-gray-50/80 text-gray-500 font-semibold">
            <tr className="border-b border-gray-200/80">
              {(grid?.columns ?? []).map((c) => (
                <th key={c.header} className="py-2 px-3 whitespace-nowrap">{c.header}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr><td colSpan={grid?.columns.length || 1} className="py-4 text-center text-gray-400">Loading…</td></tr>
            ) : grid && grid.vendors.length > 0 ? (
              grid.vendors.map((row) => (
                <tr key={row.vendor_id}>
                  {grid.columns.map((c) => (
                    <td key={c.header} className="py-2 px-3 whitespace-nowrap">{renderCell(c, row)}</td>
                  ))}
                </tr>
              ))
            ) : (
              <tr><td colSpan={grid?.columns.length || 1} className="py-4 text-center text-gray-400">No vendors.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
