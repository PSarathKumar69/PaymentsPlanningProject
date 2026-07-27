import React, { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { getVendorAging } from '../api/vendors';
import { getVendorMinFundsRequired } from '../api/newModel2';
import { postPayment } from '../api/payments';
import { postTalkingScripts } from '../api/aiCompanion';
import { ApiError } from '../api/client';
import { PlanAllocationRow, Vendor, VendorAging, VendorMinFundsRequired } from '../types';
import { ALLOCATION_STATUS_ZERO, STATUS_LABEL } from '../constants/enums';
import { ToastVariant } from './NotificationToast';
import { ConfirmModal } from './ConfirmModal';
import { SuccessCheck } from './SuccessCheck';
import { formatMoney, formatMonthShortYear } from '../utils/format';

// Deterministic template sentence (docs/14) — NOT an LLM call (CLAUDE.md
// rule 3). Mirrors test_ui.html's nm2MinFundsSentence() exactly.
function minFundsSentence(detail: VendorMinFundsRequired): string {
  const fmt = formatMoney;
  if (detail.rule.startsWith('commitment_')) {
    return `Opening balance ${fmt(detail.opening_balance)} ÷ ${detail.commitment_months} months remaining = ${fmt(detail.total)}.`;
  }
  const cur = detail.current_month ? formatMonthShortYear(detail.current_month) : null;
  const old = detail.oldest_month ? formatMonthShortYear(detail.oldest_month) : null;
  switch (detail.rule) {
    case 'v2_only_current':
      return `This month's bill: ${cur}, ${fmt(detail.current_amount)}. No older leftover outstanding. Min Funds Required: ${fmt(detail.total)}.`;
    case 'v2_current_zero_oldest_only':
      return `No bill this cycle (${cur}). Oldest outstanding bill: ${old}, ${fmt(detail.oldest_amount)}. Min Funds Required: ${fmt(detail.total)}.`;
    case 'v2_oldest_and_current':
      return `Oldest bill: ${old}, ${fmt(detail.oldest_amount)}. This month's bill: ${cur}, ${fmt(detail.current_amount)}. ` +
        `${fmt(detail.oldest_amount)} is 50% or less of ${fmt(detail.current_amount)}, so both are included. Min Funds Required: ${fmt(detail.total)}.`;
    case 'v2_oldest_and_second': {
      const second = detail.second_month ? formatMonthShortYear(detail.second_month) : '—';
      return `Oldest bill: ${old}, ${fmt(detail.oldest_amount)}. Next outstanding bill: ${second}, ${fmt(detail.second_amount)}. ` +
        `${fmt(detail.oldest_amount)} is 50% or less of this month's ${fmt(detail.current_amount)} bill, so the oldest and next ` +
        `outstanding bill are included — this month's own ${fmt(detail.current_amount)} bill is not. Min Funds Required: ${fmt(detail.total)}.`;
    }
    case 'v2_oldest_only':
      return `Oldest bill: ${old}, ${fmt(detail.oldest_amount)}. This is more than 50% of this month's ${fmt(detail.current_amount)} bill, ` +
        `so only the oldest is included. Min Funds Required: ${fmt(detail.total)}.`;
    default:
      return `No outstanding balance. Min Funds Required: ${fmt(0)}.`;
  }
}

interface VendorDetailModalProps {
  vendor: Vendor;
  showPayment: boolean;
  planningMonth: string; // whatever's currently picked on Card 1 — used only before this cycle's first Generate
  hasGeneratedThisCycle: boolean;
  latestAllocation: PlanAllocationRow | null; // this vendor's row in New Model 2's latest plan_run, if any
  effectiveAmount: number | null; // override-if-set else suggested — prefills the payment amount
  onClose: () => void;
  onPaymentLogged: (paymentStatus: string) => void;
  onCommitmentMonthsChange: (value: number | null) => void | Promise<void>;
  onNotify?: (message: string, variant?: ToastVariant) => void;
}

export const VendorDetailModal: React.FC<VendorDetailModalProps> = ({
  vendor,
  showPayment,
  planningMonth,
  hasGeneratedThisCycle,
  latestAllocation,
  effectiveAmount,
  onClose,
  onPaymentLogged,
  onCommitmentMonthsChange,
  onNotify,
}) => {
  const [aging, setAging] = useState<VendorAging | null>(null);
  const [minFunds, setMinFunds] = useState<VendorMinFundsRequired | null>(null);
  const [minFundsError, setMinFundsError] = useState('');
  const [amount, setAmount] = useState('');
  const [comment, setComment] = useState('');
  const [scriptText, setScriptText] = useState('');
  const [scriptLoading, setScriptLoading] = useState(false);
  const [showPayConfirm, setShowPayConfirm] = useState(false);
  const [paySuccessStatus, setPaySuccessStatus] = useState<string | null>(null);

  useEffect(() => {
    getVendorAging(vendor.id).then(setAging);
    if (!showPayment) {
      // Min Funds Required / AI talking script are "view details" concerns
      // only — the Pay flow (showPayment) deliberately shows just Aging +
      // Amount + Comment (Sarath's explicit call), so skip fetching what
      // won't render anyway.
      if (!hasGeneratedThisCycle && !planningMonth) {
        setMinFundsError('Pick a planning month on the New Model 2 card above first.');
      } else {
        getVendorMinFundsRequired(vendor.id, hasGeneratedThisCycle ? undefined : planningMonth)
          .then(setMinFunds)
          .catch((e) => setMinFundsError(e instanceof ApiError ? e.message : String(e)));
      }
    }
    if (effectiveAmount != null) setAmount(String(Math.round(effectiveAmount)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vendor.id]);

  const status = latestAllocation?.status;
  const parsedAmount = Number(amount.replace(/[^0-9]/g, ''));

  const submitPayment = async () => {
    setShowPayConfirm(false);
    try {
      const result = await postPayment(vendor.id, parsedAmount, comment.trim() || undefined);
      setPaySuccessStatus(result.payment_status);
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    }
  };

  const handleGenerateScript = async () => {
    if (!latestAllocation) return;
    setScriptLoading(true);
    try {
      const resp = await postTalkingScripts([latestAllocation]);
      setScriptText(resp.scripts[0]?.script_text || '');
    } catch (e) {
      onNotify?.(e instanceof ApiError ? e.message : String(e), 'error');
    } finally {
      setScriptLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-8 overflow-auto no-scrollbar" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-2xl border border-gray-200 shadow-xl w-full max-w-3xl p-6 relative">
        <button onClick={onClose} className="absolute top-3 right-4 text-gray-400 hover:text-gray-700 cursor-pointer" aria-label="Close">
          <X className="w-5 h-5" />
        </button>
        <h2 className="text-base font-bold text-gray-900 mb-4">{vendor.vendor_name} ({vendor.erp_code})</h2>

        {!showPayment && (
          <div className="flex items-center gap-2 mb-4">
            <label className="text-xs text-gray-500">Commitment Months</label>
            <input
              key={`commitment-${vendor.id}-${vendor.commitment_months ?? 'none'}`}
              type="number"
              min={1}
              step={1}
              defaultValue={vendor.commitment_months ?? ''}
              onBlur={(e) => onCommitmentMonthsChange(e.target.value === '' ? null : Number(e.target.value))}
              title="Only meaningful for Commitment-category vendors — opening balance ÷ this = the required-amount formula"
              className="w-20 border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
            />
          </div>
        )}

        <div className="flex flex-col gap-4 mb-4">
          <div className="bg-blue-50/60 rounded-lg p-3.5">
            <h3 className="text-xs font-bold text-[#0c447c] mb-2">Aging</h3>
            {!aging ? (
              <p className="text-xs text-gray-400">Loading…</p>
            ) : aging.monthly_breakdown.length === 0 ? (
              <p className="text-xs text-gray-400">No outstanding balance.</p>
            ) : (
              <div className="overflow-x-auto no-scrollbar">
                <table className="text-xs">
                  <tbody>
                    <tr>{aging.monthly_breakdown.map((m, i) => <th key={`${m.month}-${i}`} className="px-2.5 py-1 text-[#0c447c] font-semibold text-right">{formatMonthShortYear(m.month)}</th>)}</tr>
                    <tr>{aging.monthly_breakdown.map((m, i) => <th key={`${m.month}-${i}`} className="px-2.5 py-1 font-medium text-right">{m.label}</th>)}</tr>
                    <tr>{aging.monthly_breakdown.map((m, i) => <td key={`${m.month}-${i}`} className="px-2.5 py-1 font-semibold text-right">{formatMoney(m.amount)}</td>)}</tr>
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {!showPayment && (
            <div className="bg-blue-50/60 rounded-lg p-3.5">
              <h3 className="text-xs font-bold text-[#0c447c] mb-2">Min Funds Required</h3>
              {minFundsError ? (
                <p className="text-xs text-gray-400">{minFundsError}</p>
              ) : !minFunds ? (
                <p className="text-xs text-gray-400">Loading…</p>
              ) : (
                <p className="text-xs text-gray-700">{minFundsSentence(minFunds)}</p>
              )}
            </div>
          )}

          {!showPayment && status === ALLOCATION_STATUS_ZERO && (
            <div className="bg-blue-50/60 rounded-lg p-3.5">
              <h3 className="text-xs font-bold text-[#0c447c] mb-2">AI talking script</h3>
              <button
                type="button"
                disabled={scriptLoading}
                onClick={handleGenerateScript}
                className="px-3 py-1.5 bg-[#107c41] hover:bg-[#0d6535] disabled:opacity-50 text-white rounded-lg text-xs font-bold cursor-pointer"
              >
                {scriptLoading ? 'Generating…' : scriptText ? 'Regenerate' : 'Generate talking script'}
              </button>
              {scriptText && <pre className="text-xs whitespace-pre-wrap bg-white rounded p-2.5 mt-2">{scriptText}</pre>}
            </div>
          )}
        </div>

        {showPayment && (
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-3.5 relative">
            <h3 className="text-xs font-bold text-gray-900 mb-2">Log a payment</h3>
            <p className="text-[11px] text-gray-500 mb-3">
              Current status: {STATUS_LABEL[vendor.payment_status as keyof typeof STATUS_LABEL] || vendor.payment_status} ·
              This payment will apply to: {aging?.oldest_bucket || '—'}
            </p>
            <div className="flex flex-col gap-3">
              <div>
                <label className="text-[11px] text-gray-500 font-medium block mb-1">Amount</label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className="border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs w-40 focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
                />
              </div>
              <div>
                <label className="text-[11px] text-gray-500 font-medium block mb-1">Comment (optional)</label>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="Add a note about this payment…"
                  rows={2}
                  className="border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs w-full resize-none focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
                />
              </div>
              <div>
                <button
                  type="button"
                  disabled={!parsedAmount}
                  onClick={() => setShowPayConfirm(true)}
                  className="px-3.5 py-1.5 bg-[#107c41] hover:bg-[#0d6535] disabled:opacity-50 text-white rounded-lg text-xs font-bold cursor-pointer"
                >
                  Pay
                </button>
              </div>
            </div>

            {showPayConfirm && (
              <ConfirmModal
                title="Confirm payment"
                message={`Log ${formatMoney(parsedAmount)} for ${vendor.vendor_name}? This cannot be undone.`}
                confirmLabel="Confirm & Pay"
                onConfirm={submitPayment}
                onCancel={() => setShowPayConfirm(false)}
              />
            )}

            {paySuccessStatus !== null && (
              <SuccessCheck
                message={`Payment logged — ${STATUS_LABEL[paySuccessStatus as keyof typeof STATUS_LABEL] || paySuccessStatus}`}
                onDone={() => {
                  onPaymentLogged(paySuccessStatus);
                  onClose();
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
};
