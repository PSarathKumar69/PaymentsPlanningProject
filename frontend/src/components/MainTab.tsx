import React, { useRef, useState } from 'react';
import { UploadCloud, RotateCcw } from 'lucide-react';
import { commitUpload, revertUpload } from '../api/masterData';
import { MasterDataCommitResult } from '../types';
import { ApiError } from '../api/client';
import { ToastVariant } from './NotificationToast';
import { MasterDataGrid } from './MasterDataGrid';

interface MainTabProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
  onUploaded?: () => void;
}

interface UploadHistoryEntry {
  filename: string;
  result: MasterDataCommitResult;
}

// Upload + revert — one file pick, one action, takes effect immediately
// (no preview/confirm step, backend/ingestion/upload.py's own design).
//
// Scope note (flagged, not silently solved): the backend keeps exactly ONE
// backup slot (commit_upload()/revert_upload()), overwritten every commit,
// and stores no filename/timestamp for past uploads server-side — the
// audit log's own "excel_upload" entries are per-vendor field-change
// summaries, not a reconstructable per-upload event list. `uploadHistory`
// below is THIS SESSION's list only (the filename comes from the browser's
// own File object at upload time, never persisted) — a real cross-session
// history needs a backend change (store filename/timestamp per commit),
// not something this tab can reconstruct on its own.
export const MainTab: React.FC<MainTabProps> = ({ onNotify, onUploaded }) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isHovering, setIsHovering] = useState(false);
  const [uploadHistory, setUploadHistory] = useState<UploadHistoryEntry[]>([]);
  const [isReverting, setIsReverting] = useState(false);
  const [gridRefreshSignal, setGridRefreshSignal] = useState(0);

  const handleUpload = async (file: File) => {
    setIsUploading(true);
    try {
      const result = await commitUpload(file);
      setUploadHistory((prev) => [{ filename: file.name, result }, ...prev]);
      onNotify?.(
        `Upload committed — ${result.vendors_changed} vendor(s) changed ` +
          `(${result.new_vendor_count} new, ${result.vendors_with_changed_ledger_count} with a changed ledger figure).`,
        'success'
      );
      setGridRefreshSignal((n) => n + 1);
      onUploaded?.();
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    } finally {
      setIsUploading(false);
    }
  };

  const handleRevert = async () => {
    setIsReverting(true);
    try {
      const result = await revertUpload();
      onNotify?.(`Reverted. ${result.warning}`, 'success');
      setGridRefreshSignal((n) => n + 1);
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    } finally {
      setIsReverting(false);
    }
  };

  // Even dashed border regardless of card width/height (browsers otherwise
  // recompute the dash-to-gap ratio independently per side, which reads as
  // a visibly thinner top edge than the sides on a wide, short card like
  // this one) — four 2px-thick repeating-gradient strips tiled along each
  // edge instead of the CSS `border-style: dashed` shorthand.
  const dashColor = isDragOver || isHovering ? '#107c41' : 'rgba(16,124,65,0.4)';
  const dashedBorderStyle: React.CSSProperties = {
    backgroundImage: [
      `repeating-linear-gradient(to right, ${dashColor} 0 6px, transparent 6px 12px)`,
      `repeating-linear-gradient(to bottom, ${dashColor} 0 6px, transparent 6px 12px)`,
      `repeating-linear-gradient(to right, ${dashColor} 0 6px, transparent 6px 12px)`,
      `repeating-linear-gradient(to bottom, ${dashColor} 0 6px, transparent 6px 12px)`,
    ].join(', '),
    backgroundPosition: 'top left, top right, bottom left, top left',
    backgroundRepeat: 'repeat-x, repeat-y, repeat-x, repeat-y',
    backgroundSize: '100% 2px, 2px 100%, 100% 2px, 2px 100%',
  };

  return (
    <div className="flex flex-col min-h-full w-full gap-6 max-w-5xl mx-auto">
      <div className="flex-1 flex flex-col justify-center items-center text-center pt-6">
        <h1 className="text-4xl sm:text-[44px] md:text-[48px] font-extrabold text-[#107c41] tracking-tight leading-tight select-none">
          Vendor Payment Planning
        </h1>
        <p className="text-sm md:text-base text-gray-600 mt-2 font-medium tracking-normal w-[84%] min-w-[384px] max-w-3xl">
          Turns vendor dues, priorities, and available funds into a suggested payment plan each cycle —
          Finance reviews and approves before anything is paid.
        </p>
      </div>

      <div className="flex-1 flex items-center justify-center">
        <div
          onClick={() => !isUploading && fileInputRef.current?.click()}
          onMouseEnter={() => setIsHovering(true)}
          onMouseLeave={() => setIsHovering(false)}
          onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file) handleUpload(file);
          }}
          style={dashedBorderStyle}
          className={`w-[84%] min-w-[384px] max-w-3xl h-full ${isDragOver ? 'bg-emerald-50/20' : 'bg-white'} hover:bg-emerald-50/20 rounded-2xl p-6 flex flex-col items-center justify-center gap-3 transition-colors cursor-pointer group shadow-sm select-none relative overflow-hidden`}
        >
          <div className="absolute top-3 right-4 text-[10px] font-mono text-emerald-700/40 tracking-widest pointer-events-none select-none hidden sm:block font-semibold">
            XLSX • EXCEL ENGINE
          </div>

          <div className="w-12 h-12 rounded-xl bg-emerald-50 group-hover:bg-[#107c41] text-[#107c41] group-hover:text-white flex items-center justify-center transition-colors shadow-2xs border border-emerald-200">
            <UploadCloud className="w-6 h-6 transition-transform group-hover:scale-110" />
          </div>

          <div className="flex flex-col items-center text-center gap-1">
            <span className="text-base font-bold text-gray-900 group-hover:text-[#107c41] transition-colors">
              {isUploading ? 'Uploading…' : 'Upload Vendor Excel Master Sheet'}
            </span>
            <span className="text-xs text-gray-500 max-w-sm">
              Drag & drop your Excel sheet here or click to browse files — takes effect immediately
            </span>
          </div>

          <button
            type="button"
            disabled={isUploading}
            onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
            className="mt-1 bg-[#107c41] hover:bg-[#0d6535] active:scale-[0.98] disabled:opacity-50 text-white font-bold py-2.5 px-6 rounded-xl text-xs shadow-xs transition-all flex items-center gap-2 group-hover:shadow-md cursor-pointer"
          >
            <UploadCloud className="w-4 h-4 text-white shrink-0" />
            <span>Upload Excel</span>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.target.value = ''; }}
            className="hidden"
          />
        </div>
      </div>

      <div className="flex items-center justify-center mb-4">
        <div className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-xs flex flex-col gap-3 w-[84%] min-w-[384px] max-w-3xl">
          <div className="flex items-center justify-between pb-2 border-b border-gray-100">
            <h3 className="text-sm font-bold text-gray-900">Upload history</h3>
            <button
              type="button"
              disabled={isReverting}
              onClick={handleRevert}
              title="Restores the one backup slot the backend keeps"
              className="text-xs text-gray-500 hover:text-[#107c41] font-medium flex items-center gap-1.5 transition-colors cursor-pointer disabled:opacity-50"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Revert to previous upload
            </button>
          </div>
          {uploadHistory.length === 0 ? (
            <p className="text-xs text-gray-400">No upload yet this session — uploaded file names appear here as you go.</p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {uploadHistory.map((entry, i) => (
                <li key={i} className={i > 0 ? 'pt-2.5 border-t border-gray-100' : ''}>
                  <p className="text-xs text-gray-700">
                    <span className="font-semibold text-gray-900">{entry.filename}</span>
                    {' — '}
                    {entry.result.vendors_changed} vendor(s) changed ({entry.result.new_vendor_count} new,{' '}
                    {entry.result.vendors_with_changed_ledger_count} with a changed ledger figure).
                  </p>
                  {entry.result.ai_column_mapping_messages.length > 0 && (
                    <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200/60 rounded-lg p-2.5 mt-1.5">
                      {entry.result.ai_column_mapping_messages.join(' ')}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="w-full pb-4">
        <MasterDataGrid onNotify={onNotify} refreshSignal={gridRefreshSignal} />
      </div>
    </div>
  );
};
