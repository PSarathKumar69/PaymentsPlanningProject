import React from 'react';
import { AlertTriangle, HelpCircle } from 'lucide-react';

interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'danger' | 'default';
  onConfirm: () => void;
  onCancel: () => void;
  // Optional extra content rendered between the message and the button row
  // (e.g. Finalize's own inline shortfall warning) — kept generic here so
  // this component stays reusable by every other caller (Delete Plan, etc.)
  // that never passes it.
  extra?: React.ReactNode;
}

// One shared clean confirm surface for every "are you sure" moment app-wide
// (delete plan, finalize plan, log a payment, …) — replaces the native
// window.confirm() browser dialog, which can't be styled and looks
// inconsistent across browsers/OSes.
export const ConfirmModal: React.FC<ConfirmModalProps> = ({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'default',
  onConfirm,
  onCancel,
  extra,
}) => {
  const isDanger = variant === 'danger';
  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="bg-white rounded-2xl border border-gray-200 shadow-xl w-full max-w-sm p-6">
        <div
          className={`w-10 h-10 rounded-full flex items-center justify-center mb-3 ${
            isDanger ? 'bg-red-50 text-[#b42318]' : 'bg-emerald-50 text-[#107c41]'
          }`}
        >
          {isDanger ? <AlertTriangle className="w-5 h-5" /> : <HelpCircle className="w-5 h-5" />}
        </div>
        <h2 className="text-sm font-bold text-gray-900 mb-1.5">{title}</h2>
        <p className="text-xs text-gray-600 mb-5 leading-relaxed">{message}</p>
        {extra && <div className="mb-5 -mt-3">{extra}</div>}
        <div className="flex items-center justify-end gap-2.5">
          <button
            type="button"
            onClick={onCancel}
            className="px-3.5 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg text-xs font-semibold cursor-pointer"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`px-3.5 py-2 rounded-lg text-xs font-bold text-white cursor-pointer ${
              isDanger ? 'bg-[#b42318] hover:bg-[#932018]' : 'bg-[#107c41] hover:bg-[#0d6535]'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};
