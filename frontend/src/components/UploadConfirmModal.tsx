import React from 'react';

interface UploadConfirmModalProps {
  fileName: string;
  planningMonth: string;
  onPlanningMonthChange: (value: string) => void;
  isUploading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

// Dedicated modal, not a ConfirmModal extension (Main-tab upload-confirm
// task) — ConfirmModal's shape (title/message/confirm/cancel) has no room
// for an input field. Kept deliberately minimal (Sarath's call): sheet
// name + Planning Month + Confirm/Cancel, nothing else — no warning copy,
// no Sheet start month field (that override still exists server-side,
// commitUpload() just omits it now and the backend uses whatever's
// currently configured).
export const UploadConfirmModal: React.FC<UploadConfirmModalProps> = ({
  fileName,
  planningMonth,
  onPlanningMonthChange,
  isUploading,
  onConfirm,
  onCancel,
}) => {
  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => e.target === e.currentTarget && !isUploading && onCancel()}
    >
      <div className="bg-white rounded-2xl border border-gray-200 shadow-xl w-full max-w-sm p-6">
        <h2 className="text-sm font-bold text-gray-900 mb-1.5">Confirm upload</h2>
        <p className="text-xs text-gray-600 mb-4">{fileName}</p>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-gray-800">Planning Month</label>
          <input
            type="month"
            value={planningMonth}
            onChange={(e) => onPlanningMonthChange(e.target.value)}
            className="border border-gray-300 rounded-lg px-2.5 py-1.5 text-sm font-medium text-gray-900"
          />
        </div>

        <div className="flex items-center justify-end gap-2.5 mt-6">
          <button
            type="button"
            disabled={isUploading}
            onClick={onCancel}
            className="px-3.5 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={isUploading || !planningMonth}
            onClick={onConfirm}
            className="px-3.5 py-2 rounded-lg text-xs font-bold text-white bg-[#107c41] hover:bg-[#0d6535] cursor-pointer disabled:opacity-50"
          >
            {isUploading ? 'Uploading…' : 'Confirm & Upload'}
          </button>
        </div>
      </div>
    </div>
  );
};
