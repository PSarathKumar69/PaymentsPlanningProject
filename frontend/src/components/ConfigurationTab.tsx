import React, { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { addPriorityBucket, getPriorityBuckets, removePriorityBucket, updatePriorityBucket } from '../api/configuration';
import { PriorityBucket } from '../types';
import { ApiError } from '../api/client';
import { ToastVariant } from './NotificationToast';

interface ConfigurationTabProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
}

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

  const [newBucket, setNewBucket] = useState({ bucket_key: '', display_label: '', ceiling_pct: '', floor_pct: '', rotation_position: '' });

  const load = async () => {
    setLoading(true);
    try {
      setBuckets(await getPriorityBuckets());
      setLoadError('');
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleEdit = async (
    bucketKey: string,
    field: 'display_label' | 'ceiling_pct' | 'floor_pct' | 'rotation_position',
    rawValue: string
  ) => {
    const patch =
      field === 'display_label'
        ? { display_label: rawValue }
        : field === 'rotation_position'
        ? { rotation_position: parseInt(rawValue, 10) }
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
        display_label: newBucket.display_label.trim(),
        ceiling_pct: parseFloat(newBucket.ceiling_pct) / 100,
        floor_pct: parseFloat(newBucket.floor_pct) / 100,
        rotation_position: parseInt(newBucket.rotation_position, 10),
      });
      onNotify?.(`Added bucket ${newBucket.bucket_key}.`, 'success');
      setNewBucket({ bucket_key: '', display_label: '', ceiling_pct: '', floor_pct: '', rotation_position: '' });
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

  const inputCls = 'text-xs border border-gray-200 rounded px-1.5 py-1';

  return (
    <div className="flex flex-col h-full w-full max-w-4xl mx-auto gap-4 py-4 overflow-y-auto no-scrollbar">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Configuration — New Model 2 priority buckets</h1>
        <button
          type="button"
          onClick={load}
          className="px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>
      {loadError && <p className="text-xs text-red-600">ERROR loading priority buckets: {loadError}</p>}

      <div className="bg-white border border-gray-200/90 rounded-xl shadow-2xs overflow-x-auto no-scrollbar">
        <table className="w-full text-left text-xs border-separate border-spacing-0">
          <thead className="bg-gray-50/80 text-gray-500 font-semibold">
            <tr className="border-b border-gray-200/80">
              <th className="py-2 px-3">Key</th>
              <th className="py-2 px-3">Display label</th>
              <th className="py-2 px-3">Ceiling</th>
              <th className="py-2 px-3">Floor</th>
              <th className="py-2 px-3">Rotation position</th>
              <th className="py-2 px-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr><td colSpan={6} className="py-4 text-center text-gray-400">Loading…</td></tr>
            ) : (
              buckets.map((b) => (
                <tr key={b.bucket_key}>
                  <td className="py-2 px-3 font-mono">{b.bucket_key}</td>
                  <td className="py-2 px-3">
                    <input type="text" defaultValue={b.display_label} className={`${inputCls} w-28`}
                      onBlur={(e) => handleEdit(b.bucket_key, 'display_label', e.target.value)} />
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
                    <input type="number" defaultValue={b.rotation_position} className={`${inputCls} w-16`}
                      onBlur={(e) => handleEdit(b.bucket_key, 'rotation_position', e.target.value)} />
                  </td>
                  <td className="py-2 px-3">
                    <button type="button" onClick={() => handleRemove(b.bucket_key)}
                      className="px-2.5 py-1 bg-white border border-gray-300 hover:bg-gray-50 rounded-md text-[11px] font-semibold cursor-pointer">
                      Remove
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <div className="flex flex-wrap items-center gap-2 p-3 border-t border-gray-100">
          <input type="text" placeholder="Key (e.g. P6)" value={newBucket.bucket_key}
            onChange={(e) => setNewBucket({ ...newBucket, bucket_key: e.target.value })} className={`${inputCls} w-24`} />
          <input type="text" placeholder="Display label" value={newBucket.display_label}
            onChange={(e) => setNewBucket({ ...newBucket, display_label: e.target.value })} className={`${inputCls} w-32`} />
          <input type="number" placeholder="Ceiling %" value={newBucket.ceiling_pct}
            onChange={(e) => setNewBucket({ ...newBucket, ceiling_pct: e.target.value })} className={`${inputCls} w-24`} />
          <input type="number" placeholder="Floor %" value={newBucket.floor_pct}
            onChange={(e) => setNewBucket({ ...newBucket, floor_pct: e.target.value })} className={`${inputCls} w-24`} />
          <input type="number" placeholder="Rotation pos." value={newBucket.rotation_position}
            onChange={(e) => setNewBucket({ ...newBucket, rotation_position: e.target.value })} className={`${inputCls} w-24`} />
          <button type="button" onClick={handleAdd}
            className="px-3 py-1.5 bg-[#107c41] hover:bg-[#0d6535] text-white rounded-lg text-xs font-bold cursor-pointer">
            + Add bucket
          </button>
        </div>
      </div>
    </div>
  );
};
