import React from 'react';
import { X } from 'lucide-react';
import { FinalizeCheckResponse } from '../types';
import { formatMoney } from '../utils/format';

interface ShortfallModalProps {
  data: FinalizeCheckResponse;
  onClose: () => void;
  onReduceToSuggested: () => void;
  onPickAnotherVendor: () => void;
  onIncreaseFunds: () => void;
}

// The three-option block (docs/14) — the system never auto-resolves a
// funds shortfall at Finalize time. Mirrors test_ui.html's
// showNm2ShortfallModal() exactly.
export const ShortfallModal: React.FC<ShortfallModalProps> = ({
  data,
  onClose,
  onReduceToSuggested,
  onPickAnotherVendor,
  onIncreaseFunds,
}) => {
  const plural = data.responsible_vendors.length > 1;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-8 overflow-auto no-scrollbar" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-2xl border border-gray-200 shadow-xl w-full max-w-lg p-6 relative">
        <button onClick={onClose} className="absolute top-3 right-4 text-gray-400 hover:text-gray-700 cursor-pointer" aria-label="Close">
          <X className="w-5 h-5" />
        </button>
        <h2 className="text-base font-bold text-gray-900 mb-3">Plan exceeds available funds</h2>
        <p className="text-xs text-gray-600 mb-4">
          Committed amount {formatMoney(data.total_committed)} exceeds available funds {formatMoney(data.available_funds)} by{' '}
          <strong>{formatMoney(data.over_by)}</strong>.
          {data.responsible_vendors.length > 0 && (
            <> Responsible override(s): {data.responsible_vendors.map((v) => v.vendor_name).join(', ')}.</>
          )}
        </p>
        <div className="flex flex-col gap-2 items-start">
          <button onClick={onReduceToSuggested} className="px-3.5 py-2 bg-[#107c41] hover:bg-[#0d6535] text-white rounded-lg text-xs font-bold cursor-pointer">
            1. Reduce {plural ? 'these overrides' : 'this override'} back to the suggested amount
          </button>
          <button onClick={onPickAnotherVendor} className="px-3.5 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg text-xs font-semibold cursor-pointer">
            2. Reduce another vendor's amount to free up the difference
          </button>
          <button onClick={onIncreaseFunds} className="px-3.5 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg text-xs font-semibold cursor-pointer">
            3. Increase available funds to cover the gap
          </button>
        </div>
      </div>
    </div>
  );
};
