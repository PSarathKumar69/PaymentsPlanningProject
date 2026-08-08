import React, { useEffect, useState } from 'react';
import { Download, GripVertical, History, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import {
  addPriorityBucket,
  getPriorityBuckets,
  removePriorityBucket,
  reorderPriorityBuckets,
  updatePriorityBucket,
} from '../api/configuration';
import { getAuditLog } from '../api/auditLog';
import { AuditLogEntry, PriorityBucket } from '../types';
import { ApiError } from '../api/client';
import { ToastVariant } from './NotificationToast';
import { formatAuditValue, formatTimestamp } from '../utils/format';
import { AUDIT_SOURCE_OPTIONS, auditFieldLabel, auditSourceBadgeClass, auditSourceLabel } from '../constants/enums';

interface ConfigurationTabProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
}

const AUDIT_PAGE_SIZE = 50;

const vendorDisplay = (entry: AuditLogEntry): string => {
  if (entry.vendor_id == null) return 'System';
  if (entry.vendor_name) return `${entry.vendor_name} (${entry.erp_code})`;
  return `Vendor #${entry.vendor_id} (not found)`;
};

const auditSummary = (entry: AuditLogEntry): string => {
  const who = entry.vendor_name ? `${entry.vendor_name}'s` : entry.vendor_id != null ? `Vendor #${entry.vendor_id}'s` : 'The';
  const parts = [`${who} ${auditFieldLabel(entry.field_name)} changed`];
  if (entry.old_value != null) parts.push(`from ${formatAuditValue(entry.field_name, entry.old_value)}`);
  parts.push(entry.new_value != null ? `to ${formatAuditValue(entry.field_name, entry.new_value)}` : 'to nothing (cleared)');
  return `${formatTimestamp(entry.timestamp)} — ${parts.join(' ')} (${auditSourceLabel(entry.source)}).`;
};

const csvEscape = (value: string) => `"${value.replace(/"/g, '""')}"`;

// Replicates test_ui.html's loadPriorityBuckets()/priorityBucketRowHtml()/
// editPriorityBucketNow()/addPriorityBucketNow()/removePriorityBucketNow():
// system-level only (ceiling %, floor %, bucket set) — vendor-specific tags
// live on the Planning tab instead (docs/11 Task 2 Part C's scope split).
// Role-gating stays the mocked client-side placeholder docs/15 already
// scoped — no real auth/RBAC is built here.
export const ConfigurationTab: React.FC<ConfigurationTabProps> = ({ onNotify }) => {
  const [buckets, setBuckets] = useState<PriorityBucket[]>([]);
  const [loading, setLoading] = useState(false);
  // Blocking page-load failure only (the table can't render without this) —
  // stays inline, not a toast. Every other result below (edit/add/remove)
  // is a fire-and-forget action confirmation and goes through onNotify.
  const [loadError, setLoadError] = useState('');

  const [newBucket, setNewBucket] = useState({ category_name: '', bucket_key: '', ceiling_pct: '', floor_pct: '' });
  const [draggedKey, setDraggedKey] = useState<string | null>(null);

  // Auto-suggested next unused P<n> (Configuration-tab-rebuild task) —
  // suggest one past the highest numeric tag in use (P0/P1 included, so
  // this still lands past P5 as before). Finance can still freely edit it
  // before submitting.
  const nextSuggestedTag = (rows: PriorityBucket[]): string => {
    const numbers = rows
      .map((b) => /^P(\d+)$/i.exec(b.bucket_key)?.[1])
      .filter((n): n is string => n != null)
      .map(Number);
    return `P${(numbers.length ? Math.max(...numbers) : 1) + 1}`;
  };

  // Audit Log viewer (Configuration-module UI/UX task) — placed on this tab
  // rather than a new top-level nav item: this is already the "system
  // administration" surface, and a 5th tab is a bigger information-
  // architecture change than this task should make unilaterally. Own load/
  // loading/error/pagination state, independent of the bucket table above.
  const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditLoadError, setAuditLoadError] = useState('');

  const [search, setSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const rows = await getPriorityBuckets();
      setBuckets(rows);
      setNewBucket((prev) => (prev.bucket_key ? prev : { ...prev, bucket_key: nextSuggestedTag(rows) }));
      setLoadError('');
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const loadAuditLog = async (offset: number, append: boolean) => {
    setAuditLoading(true);
    try {
      const result = await getAuditLog({
        search: search.trim() || undefined,
        source: sourceFilter || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        limit: AUDIT_PAGE_SIZE,
        offset,
      });
      setAuditLog((prev) => (append ? [...prev, ...result.items] : result.items));
      setAuditTotal(result.total);
      setAuditLoadError('');
    } catch (e) {
      setAuditLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setAuditLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  // Debounced so typing a search term doesn't fire a request per keystroke;
  // source/date-range changes ride the same debounce for one code path.
  useEffect(() => {
    const timer = setTimeout(() => loadAuditLog(0, false), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, sourceFilter, dateFrom, dateTo]);

  const exportAuditCsv = () => {
    const header = ['Timestamp', 'Vendor', 'Field', 'Old value', 'New value', 'Source', 'Summary'];
    const rows = auditLog.map((e) => [
      formatTimestamp(e.timestamp),
      vendorDisplay(e),
      auditFieldLabel(e.field_name),
      formatAuditValue(e.field_name, e.old_value),
      formatAuditValue(e.field_name, e.new_value),
      auditSourceLabel(e.source),
      auditSummary(e),
    ]);
    const csv = [header, ...rows].map((row) => row.map(csvEscape).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'audit-log.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleEdit = async (
    bucketKey: string,
    field: 'category_name' | 'bucket_key' | 'ceiling_pct' | 'floor_pct',
    rawValue: string
  ) => {
    const patch =
      field === 'category_name'
        ? { category_name: rawValue }
        : field === 'bucket_key'
        ? { new_bucket_key: rawValue }
        : { [field]: parseFloat(rawValue) / 100 };
    try {
      await updatePriorityBucket(bucketKey, patch);
      onNotify?.(`Updated ${bucketKey}.`, 'success');
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    }
    await load();
  };

  const handleAdd = async () => {
    try {
      await addPriorityBucket({
        bucket_key: newBucket.bucket_key.trim(),
        display_label: newBucket.category_name.trim(),
        category_name: newBucket.category_name.trim(),
        ceiling_pct: parseFloat(newBucket.ceiling_pct) / 100,
        floor_pct: parseFloat(newBucket.floor_pct) / 100,
        rotation_position: buckets.length,
      });
      onNotify?.(`Added category ${newBucket.category_name}.`, 'success');
      setNewBucket({ category_name: '', bucket_key: '', ceiling_pct: '', floor_pct: '' });
      await load();
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    }
  };

  const handleRemove = async (bucketKey: string) => {
    if (!window.confirm(`Remove bucket ${bucketKey}? This can't be undone.`)) return;
    try {
      await removePriorityBucket(bucketKey);
      onNotify?.(`Removed bucket ${bucketKey}.`, 'success');
      await load();
    } catch (e) {
      // The backend's own error already names the blocking vendor's ERP
      // code when a bucket is still in use — shown verbatim, not replaced
      // with a generic client-side message.
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    }
  };

  // Drag-to-reorder (Configuration-tab-rebuild task) — row position IS cut
  // order now (top = cut last, bottom = cut first). Native HTML5 D&D, no
  // extra dependency. Reorders optimistically, then persists via the new
  // reorder endpoint; a failure reloads the real order from the server.
  const handleDrop = async (targetKey: string) => {
    const fromKey = draggedKey;
    setDraggedKey(null);
    if (!fromKey || fromKey === targetKey) return;
    const reordered = [...buckets];
    const fromIndex = reordered.findIndex((b) => b.bucket_key === fromKey);
    const toIndex = reordered.findIndex((b) => b.bucket_key === targetKey);
    if (fromIndex === -1 || toIndex === -1) return;
    const [moved] = reordered.splice(fromIndex, 1);
    reordered.splice(toIndex, 0, moved);
    setBuckets(reordered);
    try {
      // Must Pay/Commitment are ordinary reorderable rows now (P0/P1
      // edit-lock removed task) — the full order, every row included.
      await reorderPriorityBuckets(reordered.map((b) => b.bucket_key));
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
      await load();
    }
  };

  const inputCls =
    'text-xs border border-gray-200 rounded px-1.5 py-1 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41] transition-colors';

  return (
    <div className="flex flex-col h-full w-full gap-6 py-4 overflow-y-auto thin-scrollbar">
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-full bg-emerald-50 text-[#107c41] border border-emerald-200/80 flex items-center justify-center shrink-0">
          <ShieldCheck className="w-4.5 h-4.5" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-gray-900">Configuration</h1>
          <p className="text-xs text-gray-500">Priority buckets and every override, in one place.</p>
        </div>
      </div>

      {/* ---- Categories / priority tags ---------------------------------- */}
      <div className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-xs flex flex-col gap-3 w-full">
        <div className="flex items-center justify-between pb-2 border-b border-gray-100">
          <h3 className="text-sm font-bold text-gray-900">Categories &amp; priority tags</h3>
          <button
            type="button"
            onClick={load}
            className="text-xs text-gray-500 hover:text-[#107c41] font-medium cursor-pointer whitespace-nowrap flex items-center gap-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
        {loadError && <p className="text-xs text-red-600">ERROR loading categories: {loadError}</p>}

        <div className="overflow-x-auto thin-scrollbar border border-gray-100 rounded-lg">
          <table className="w-full text-left text-xs border-separate border-spacing-0">
            <thead className="bg-emerald-50/70 text-[#107c41] font-semibold">
              <tr className="border-b border-emerald-100">
                <th className="py-2 px-3 w-6"></th>
                <th className="py-2 px-3">Category name</th>
                <th className="py-2 px-3">Priority tag</th>
                <th className="py-2 px-3">Ceiling</th>
                <th className="py-2 px-3">Floor</th>
                <th className="py-2 px-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr><td colSpan={6} className="py-4 text-center text-gray-400">Loading…</td></tr>
              ) : buckets.length === 0 ? (
                <tr><td colSpan={6} className="py-4 text-center text-gray-400">No categories configured.</td></tr>
              ) : (
                buckets.map((b, i) => (
                  <tr
                    key={b.bucket_key}
                    draggable
                    onDragStart={() => setDraggedKey(b.bucket_key)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => handleDrop(b.bucket_key)}
                    className={`${i % 2 === 1 ? 'bg-gray-50/40' : 'bg-white'} hover:bg-emerald-50/40 transition-colors ${draggedKey === b.bucket_key ? 'opacity-40' : ''} cursor-grab`}
                  >
                    <td className="py-2 px-3 text-gray-400">
                      <GripVertical className="w-3.5 h-3.5" />
                    </td>
                    <td className="py-2 px-3">
                      <input type="text" defaultValue={b.category_name} className={`${inputCls} w-32`}
                        onBlur={(e) => handleEdit(b.bucket_key, 'category_name', e.target.value)} />
                    </td>
                    <td className="py-2 px-3">
                      <input type="text" defaultValue={b.bucket_key} className={`${inputCls} w-20 font-mono`}
                        onBlur={(e) => handleEdit(b.bucket_key, 'bucket_key', e.target.value)} />
                    </td>
                    <td className="py-2 px-3">
                      <input type="number" defaultValue={(b.ceiling_pct * 100).toFixed(2)} className={`${inputCls} w-20`}
                        onBlur={(e) => handleEdit(b.bucket_key, 'ceiling_pct', e.target.value)} /> %
                    </td>
                    <td className="py-2 px-3">
                      <input type="number" defaultValue={(b.floor_pct * 100).toFixed(2)} className={`${inputCls} w-20`}
                        onBlur={(e) => handleEdit(b.bucket_key, 'floor_pct', e.target.value)} /> %
                    </td>
                    <td className="py-2 px-3">
                      <button type="button" onClick={() => handleRemove(b.bucket_key)} disabled={!b.deletable}
                        title={b.deletable ? undefined : 'Guaranteed-funding bucket — cannot be removed'}
                        className="px-2.5 py-1 bg-white border border-gray-300 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white rounded-lg text-[11px] font-semibold cursor-pointer">
                        Remove
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          <div className="flex flex-wrap items-center gap-2 p-3 border-t border-gray-100 bg-gray-50/40">
            <input type="text" placeholder="Category name (e.g. Contractor)" value={newBucket.category_name}
              onChange={(e) => setNewBucket({ ...newBucket, category_name: e.target.value })} className={`${inputCls} w-44`} />
            <input type="text" placeholder="Tag" value={newBucket.bucket_key}
              onChange={(e) => setNewBucket({ ...newBucket, bucket_key: e.target.value })} className={`${inputCls} w-16 font-mono`} />
            <input type="number" placeholder="Ceiling %" value={newBucket.ceiling_pct}
              onChange={(e) => setNewBucket({ ...newBucket, ceiling_pct: e.target.value })} className={`${inputCls} w-24`} />
            <input type="number" placeholder="Floor %" value={newBucket.floor_pct}
              onChange={(e) => setNewBucket({ ...newBucket, floor_pct: e.target.value })} className={`${inputCls} w-24`} />
            <button type="button" onClick={handleAdd}
              className="px-3 py-1.5 bg-[#107c41] hover:bg-[#0d6535] text-white rounded-lg text-xs font-bold cursor-pointer">
              + Add category
            </button>
          </div>
        </div>
      </div>

      {/* ---- Audit log --------------------------------------------------- */}
      <div className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-xs flex flex-col gap-3 w-full">
        <div className="flex items-center justify-between pb-2 border-b border-gray-100 gap-3 flex-wrap">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-full bg-emerald-50 text-[#107c41] border border-emerald-200/80 flex items-center justify-center shrink-0">
              <History className="w-3.5 h-3.5" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-900">Audit log</h3>
              <p className="text-[11px] text-gray-500">Every override — who/what changed, from what, to what, and how.</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={exportAuditCsv} disabled={auditLog.length === 0}
              className="px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed text-gray-700 rounded-lg text-xs font-semibold flex items-center gap-1.5 cursor-pointer">
              <Download className="w-3.5 h-3.5" />
              Export CSV
            </button>
            <button type="button" onClick={() => loadAuditLog(0, false)}
              className="px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg text-xs font-semibold flex items-center gap-1.5 cursor-pointer">
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by vendor / ERP code…"
              className="pl-7 pr-3 py-1.5 bg-gray-50/80 border border-gray-200 rounded-lg text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41] w-56"
            />
          </div>
          <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-gray-50/80 text-gray-700 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41]">
            <option value="">All sources</option>
            {AUDIT_SOURCE_OPTIONS.map((s) => <option key={s} value={s}>{auditSourceLabel(s)}</option>)}
          </select>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-gray-50/80 text-gray-700 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41]" />
          <span className="text-xs text-gray-400">to</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-gray-50/80 text-gray-700 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41]" />
        </div>

        {auditLoadError && <p className="text-xs text-red-600">ERROR loading audit log: {auditLoadError}</p>}

        {/* Finance cares about who/what changed, when, from->to, and why —
            not a restated prose sentence of the same four facts, which was
            the real reason every row grew tall and the table looked bad.
            Dropped the Summary column outright rather than just visually
            shrinking it. Old -> New is single-line + truncated with a
            native title tooltip for the rare long value, instead of
            wrapping and inflating row height. */}
        <div className="table-scroll overscroll-contain max-h-96 border border-gray-100 rounded-lg">
          <table className="w-full text-left text-xs border-separate border-spacing-0">
            <thead className="bg-emerald-50/70 text-[#107c41] font-semibold sticky top-0 z-10">
              <tr className="border-b border-emerald-100">
                <th className="py-2 px-3 whitespace-nowrap bg-emerald-50/70">Timestamp</th>
                <th className="py-2 px-3 bg-emerald-50/70">Vendor</th>
                <th className="py-2 px-3 bg-emerald-50/70">Field</th>
                <th className="py-2 px-3 bg-emerald-50/70">Old -&gt; New</th>
                <th className="py-2 px-3 bg-emerald-50/70">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {auditLoading && auditLog.length === 0 ? (
                <tr><td colSpan={5} className="py-4 text-center text-gray-400">Loading…</td></tr>
              ) : auditLog.length === 0 ? (
                <tr><td colSpan={5} className="py-4 text-center text-gray-400">No overrides logged yet.</td></tr>
              ) : (
                auditLog.map((entry, i) => (
                  <tr key={entry.id} className={`${i % 2 === 1 ? 'bg-gray-50/40' : 'bg-white'} hover:bg-emerald-50/40 transition-colors`} title={auditSummary(entry)}>
                    <td className="py-2 px-3 whitespace-nowrap text-gray-600">{formatTimestamp(entry.timestamp)}</td>
                    <td className="py-2 px-3 text-gray-700 whitespace-nowrap truncate max-w-40">{vendorDisplay(entry)}</td>
                    <td className="py-2 px-3 whitespace-nowrap">{auditFieldLabel(entry.field_name)}</td>
                    <td className="py-2 px-3 text-gray-700 whitespace-nowrap truncate max-w-xs">
                      {formatAuditValue(entry.field_name, entry.old_value)} -&gt; {formatAuditValue(entry.field_name, entry.new_value)}
                    </td>
                    <td className="py-2 px-3 whitespace-nowrap">
                      <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap ${auditSourceBadgeClass(entry.source)}`}>
                        {auditSourceLabel(entry.source)}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {auditLog.length > 0 && (
          <div className="flex items-center justify-between text-[11px] text-gray-400">
            <span>Showing {auditLog.length} of {auditTotal}</span>
            {auditLog.length < auditTotal && (
              <button type="button" onClick={() => loadAuditLog(auditLog.length, true)} disabled={auditLoading}
                className="text-xs text-gray-500 hover:text-[#107c41] font-medium cursor-pointer disabled:opacity-50">
                {auditLoading ? 'Loading…' : 'Load more'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
