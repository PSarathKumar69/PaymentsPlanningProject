import React, { useRef, useState } from 'react';
import { UploadCloud, RotateCcw } from 'lucide-react';
import { commitUpload, revertUpload } from '../api/masterData';
import { getCurrentPlanningMonth, getSuggestedPlanningMonth } from '../api/newModel2';
import { ApiError } from '../api/client';
import { ToastVariant } from './NotificationToast';
import { UploadConfirmModal } from './UploadConfirmModal';
import { MasterDataGrid } from './MasterDataGrid';

interface MainTabProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
  onUploaded?: () => void;
}

const currentMonthValue = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

// Upload flow (revised): picking/dropping a file no longer commits
// immediately — it opens UploadConfirmModal first, pre-filled with the
// current cycle's planning month (or the sheet's suggested next month if
// none is set yet). Only Confirm & Upload actually calls commitUpload();
// Cancel discards the picked file with no request made. Kept deliberately
// minimal (Sarath's call): no Sheet start month field in the modal — that
// override still exists server-side (commitUpload()'s optional param),
// just no longer surfaced here; the backend uses whatever's configured.
//
// Upload history card removed (Sarath's call — this session's own list was
// never more than a same-session filename feed anyway, see the old
// Scope note this replaced: the backend keeps exactly ONE backup slot
// (commit_upload()/revert_upload()), overwritten every commit, and stores
// no filename/timestamp for past uploads server-side, so there was never a
// real cross-session history to show here). The upload card now spans the
// full width; "Revert to previous upload" moved into its header, and the
// most recent upload's AI-column-mapping warning (if any) renders as its
// own banner right below the card instead of inside a per-upload history
// list — CLAUDE.md's "loud but non-blocking" banner still needs somewhere
// to live even without a history feed.
export const MainTab: React.FC<MainTabProps> = ({ onNotify, onUploaded }) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isHovering, setIsHovering] = useState(false);
  const [lastUploadWarnings, setLastUploadWarnings] = useState<string[]>([]);
  const [isReverting, setIsReverting] = useState(false);
  // Bumped on every successful upload/revert — MasterDataGrid.tsx's own
  // refetch signal, scoped to this tab (separate from App.tsx's
  // refreshSignal, which drives PlanningView instead).
  const [gridRefreshSignal, setGridRefreshSignal] = useState(0);

  // ---- upload confirm modal ------------------------------------------------
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [planningMonthInput, setPlanningMonthInput] = useState(currentMonthValue());

  const openConfirmModal = async (file: File) => {
    setPendingFile(file);
    // Pre-fill Planning Month: current cycle's already-set value if one
    // exists, else the sheet's suggested next month — never left blank.
    try {
      const current = await getCurrentPlanningMonth();
      if (current.planning_month) {
        setPlanningMonthInput(current.planning_month);
      } else {
        const suggested = await getSuggestedPlanningMonth();
        if (suggested.suggested_planning_month) setPlanningMonthInput(suggested.suggested_planning_month);
      }
    } catch {
      // Leave the calendar-default pre-fill in place — Finance can still edit it.
    }
  };

  const handleConfirmUpload = async () => {
    if (!pendingFile) return;
    setIsUploading(true);
    try {
      const result = await commitUpload(pendingFile, planningMonthInput);
      setLastUploadWarnings(result.ai_column_mapping_messages);
      setGridRefreshSignal((n) => n + 1);
      // Month-end cycle reset (merge-rollover-into-upload task): only
      // mentioned when it actually happened — the ordinary same-month
      // correction case stays exactly as quiet as it was before this task,
      // no "nothing reset" clutter.
      let message =
        `Upload committed — ${result.vendors_changed} vendor(s) changed ` +
        `(${result.new_vendor_count} new, ${result.vendors_with_changed_ledger_count} with a changed ledger figure).`;
      if (result.cycle_reset) {
        message += ` New payment cycle started — ${result.vendors_reset} vendor(s) reset for the new month.`;
      }
      onNotify?.(message, 'success');
      setPendingFile(null);
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
      setGridRefreshSignal((n) => n + 1);
      onNotify?.(`Reverted. ${result.warning}`, 'success');
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
    <div className="flex flex-col min-h-full w-full gap-6">
      <div className="flex-1 flex flex-col justify-center items-center text-center pt-6">
        <h1 className="text-4xl sm:text-[44px] md:text-[48px] font-extrabold text-[#107c41] tracking-tight leading-tight select-none">
          Vendor Payment Planning
        </h1>
        <p className="text-sm md:text-base text-gray-600 mt-2 font-medium tracking-normal w-[84%] min-w-[384px] max-w-3xl">
          Turns vendor dues, priorities, and available funds into a suggested payment plan each cycle —
          Finance reviews and approves before anything is paid.
        </p>
      </div>

      {pendingFile && (
        <UploadConfirmModal
          fileName={pendingFile.name}
          planningMonth={planningMonthInput}
          onPlanningMonthChange={setPlanningMonthInput}
          isUploading={isUploading}
          onConfirm={handleConfirmUpload}
          onCancel={() => !isUploading && setPendingFile(null)}
        />
      )}

      {/* Full width: upload card — drag-drop area + Upload Excel button, with
          Revert tucked into its top-left corner (Upload history card removed
          — Sarath's call, see the comment above the component). */}
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
          if (file) openConfirmModal(file);
        }}
        style={dashedBorderStyle}
        className={`min-h-56 ${isDragOver ? 'bg-emerald-50/20' : 'bg-white'} hover:bg-emerald-50/20 rounded-2xl p-6 flex flex-col items-center justify-center gap-3 transition-colors cursor-pointer group shadow-sm select-none relative overflow-hidden`}
      >
        <div className="absolute top-3 right-4 text-[10px] font-mono text-emerald-700/40 tracking-widest pointer-events-none select-none hidden sm:block font-semibold">
          XLSX • EXCEL ENGINE
        </div>

        <button
          type="button"
          disabled={isReverting}
          onClick={(e) => { e.stopPropagation(); handleRevert(); }}
          title="Restores the one backup slot the backend keeps"
          className="absolute top-3 left-4 text-xs text-gray-400 hover:text-[#107c41] font-medium flex items-center gap-1.5 transition-colors cursor-pointer disabled:opacity-50"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Revert to previous upload
        </button>

        <div className="w-12 h-12 rounded-xl bg-emerald-50 group-hover:bg-[#107c41] text-[#107c41] group-hover:text-white flex items-center justify-center transition-colors shadow-2xs border border-emerald-200">
          <UploadCloud className="w-6 h-6 transition-transform group-hover:scale-110" />
        </div>

        <div className="flex flex-col items-center text-center gap-1">
          <span className="text-base font-bold text-gray-900 group-hover:text-[#107c41] transition-colors">
            Upload Vendor Excel Master Sheet
          </span>
          <span className="text-xs text-gray-500 max-w-sm">
            Drag & drop your Excel sheet here or click to browse files
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
          onChange={(e) => { const f = e.target.files?.[0]; if (f) openConfirmModal(f); e.target.value = ''; }}
          className="hidden"
        />
      </div>

      {/* Most recent upload's AI-column-mapping warning, if any — loud but
          non-blocking (CLAUDE.md), previously shown per-entry inside the
          now-removed Upload history list. */}
      {lastUploadWarnings.length > 0 && (
        <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200/60 rounded-lg p-2.5">
          {lastUploadWarnings.join(' ')}
        </div>
      )}

      {/* Full width below: the real Master Grid (docs/18 — no longer dead code). */}
      <MasterDataGrid onNotify={onNotify} refreshSignal={gridRefreshSignal} />
    </div>
  );
};
