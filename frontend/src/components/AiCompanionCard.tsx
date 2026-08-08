import React, { useEffect, useRef, useState } from 'react';
import { Copy, RefreshCw, X } from 'lucide-react';
import { postVendorTalkingPoints } from '../api/aiCompanion';
import { ApiError } from '../api/client';
import { Vendor, VendorTalkingPointsResult } from '../types';
import { formatMoney, formatPct } from '../utils/format';
import {
  AGING_BUCKET_BADGE_CLASS,
  AgingBucket,
  ALLOCATION_STATUS_BADGE_CLASS,
  ALLOCATION_STATUS_LABEL,
  AllocationStatus,
  categoryBadgeClass,
  categoryLabel,
} from '../constants/enums';

interface AiCompanionCardProps {
  // Vendors eligible for the search step (the current cycle's plan) — unused
  // when preselectedVendor is set, since that entry path skips the picker.
  planVendors: Vendor[];
  preselectedVendor: Vendor | null;
  onClose: () => void;
  // 'popup': narrow vertical card anchored above the floating FAB, no dim
  // backdrop (CompanionPanel's own entry point). 'modal': centered overlay
  // (VendorDetailModal's "Ask AI" link, layered on top of its own modal).
  variant?: 'popup' | 'modal';
}

type Step = 'pick' | 'chosen';
type Format = 'talking' | 'email';

// Each tab (Talking/Email) fetches and caches independently — switching to
// an already-fetched tab never re-calls Gemini; switching to a fresh one
// fetches lazily, only on first visit (Sarath's cost/latency call: don't
// eagerly fetch both formats on open).
interface TabState {
  status: 'idle' | 'loading' | 'result' | 'error';
  result?: VendorTalkingPointsResult;
  error?: string;
}
const IDLE_TAB: TabState = { status: 'idle' };

const MAX_MATCHES = 10;

const chip = (cls: string, text: string) => (
  <span className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ${cls}`}>{text}</span>
);

// One consolidated AI card (docs: AI screen revamp) — reused by CompanionPanel's
// floating-button search flow and VendorDetailModal's "Ask AI" link. Both
// entry paths hit the same any-status POST /ai/vendor-talking-points, which
// already returns the deterministic-layer fact pack (category/priority/
// status/required/allocated/aging bucket) alongside the AI narrative.
export const AiCompanionCard: React.FC<AiCompanionCardProps> = ({ planVendors, preselectedVendor, onClose, variant = 'modal' }) => {
  const [step, setStep] = useState<Step>(preselectedVendor ? 'chosen' : 'pick');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Vendor | null>(preselectedVendor);
  const [activeFormat, setActiveFormat] = useState<Format>('talking');
  const [tabs, setTabs] = useState<Record<Format, TabState>>({ talking: IDLE_TAB, email: IDLE_TAB });
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchFormat = async (vendor: Vendor, format: Format) => {
    setTabs((prev) => ({ ...prev, [format]: { status: 'loading' } }));
    try {
      const resp = await postVendorTalkingPoints(vendor.id, format);
      setTabs((prev) => ({ ...prev, [format]: { status: 'result', result: resp } }));
    } catch (e) {
      setTabs((prev) => ({ ...prev, [format]: { status: 'error', error: e instanceof ApiError ? e.message : String(e) } }));
    }
  };

  const fetchFor = (vendor: Vendor) => {
    setSelected(vendor);
    setStep('chosen');
    setActiveFormat('talking');
    setTabs({ talking: IDLE_TAB, email: IDLE_TAB });
    fetchFormat(vendor, 'talking');
  };

  useEffect(() => {
    if (preselectedVendor) fetchFormat(preselectedVendor, 'talking');
    return () => { if (copyTimer.current) clearTimeout(copyTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startOver = () => {
    setStep('pick');
    setSelected(null);
    setTabs({ talking: IDLE_TAB, email: IDLE_TAB });
    setActiveFormat('talking');
    setQuery('');
  };

  // Lazy per-tab fetch: only calls Gemini the first time a tab is opened;
  // switching back to an already-fetched tab just re-shows its cached state.
  const handleTabSwitch = (format: Format) => {
    setActiveFormat(format);
    if (selected && tabs[format].status === 'idle') fetchFormat(selected, format);
  };

  const activeTab = tabs[activeFormat];
  // Header/key-facts row is shared between tabs (identical fact-pack fields
  // regardless of format) — shown from whichever tab has already loaded, so
  // switching to a not-yet-fetched tab still shows it immediately while only
  // the narrative area shows a loading skeleton.
  const displayResult = activeTab.result ?? tabs[activeFormat === 'talking' ? 'email' : 'talking'].result;

  const handleCopy = () => {
    if (!activeTab.result) return;
    navigator.clipboard.writeText(activeTab.result.script_text);
    setCopied(true);
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(false), 1500);
  };

  const handleRegenerate = () => {
    if (selected) fetchFormat(selected, activeFormat);
  };

  const matches = query.trim()
    ? planVendors
        .filter((v) => v.vendor_name.toLowerCase().includes(query.trim().toLowerCase()) || v.erp_code.toLowerCase().includes(query.trim().toLowerCase()))
        .slice(0, MAX_MATCHES)
    : [];

  const isPopup = variant === 'popup';

  return (
    <div
      className={isPopup
        ? 'fixed inset-0 z-[60]'
        : 'fixed inset-0 z-[60] flex items-start justify-center bg-black/40 p-8 overflow-auto thin-scrollbar'}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={isPopup
          ? 'fixed bottom-24 right-6 bg-white rounded-2xl border border-gray-200 shadow-2xl w-96 max-h-[calc(100vh-8rem)] overflow-y-auto thin-scrollbar p-5'
          : 'bg-white rounded-2xl border border-gray-200 shadow-xl w-full max-w-2xl p-6 relative'}
      >
        <button onClick={onClose} className="absolute top-3 right-4 text-gray-400 hover:text-gray-700 cursor-pointer" aria-label="Close">
          <X className="w-5 h-5" />
        </button>

        <h2 className="text-base font-bold text-gray-900 mb-4">Ask AI about a vendor</h2>

        {step === 'pick' && (
          planVendors.length === 0 ? (
            <p className="text-sm text-gray-400">Generate a plan first, then come back for talking points.</p>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-gray-500">Which vendor do you want talking points for?</p>
              <input
                autoFocus
                type="text"
                placeholder="Vendor name or ERP code…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#107c41]/30 focus:border-[#107c41]"
              />
              <div className="flex flex-col gap-1 max-h-64 overflow-y-auto thin-scrollbar">
                {matches.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => fetchFor(v)}
                    className="text-left text-sm px-3 py-2 border border-gray-200 rounded-lg hover:bg-gray-50 cursor-pointer"
                  >
                    {v.vendor_name} ({v.erp_code})
                  </button>
                ))}
                {query.trim() && matches.length === 0 && <p className="text-xs text-gray-400">No matching vendor.</p>}
              </div>
            </div>
          )
        )}

        {step === 'chosen' && (
          <div className="flex flex-col gap-4">
            {!preselectedVendor && (
              <button onClick={startOver} className="text-xs text-[#107c41] font-semibold cursor-pointer self-start">&larr; Back</button>
            )}

            {displayResult && (
              <div>
                <h3 className="text-sm font-bold text-gray-900 mb-1.5">{displayResult.vendor_name} ({displayResult.erp_code})</h3>
                <div className="flex flex-wrap gap-1.5">
                  {chip(categoryBadgeClass(displayResult.category), categoryLabel(displayResult.category))}
                  {displayResult.priority_tag && chip(categoryBadgeClass(displayResult.category), displayResult.priority_tag)}
                  {chip(ALLOCATION_STATUS_BADGE_CLASS[displayResult.status as AllocationStatus] || 'bg-gray-100 text-gray-700', ALLOCATION_STATUS_LABEL[displayResult.status as AllocationStatus] || displayResult.status)}
                </div>
              </div>
            )}

            {displayResult && (
              <div className={isPopup ? 'bg-blue-50/60 rounded-lg p-3.5 flex flex-col gap-2' : 'bg-blue-50/60 rounded-lg p-3.5 grid grid-cols-2 sm:grid-cols-4 gap-3'}>
                {[
                  ['Required', formatMoney(displayResult.required_amount)],
                  ['Allocated', formatMoney(displayResult.allocated_amount)],
                  ['% funded', formatPct(displayResult.allocated_amount, displayResult.required_amount)],
                ].map(([label, value]) => (
                  isPopup ? (
                    <div key={label} className="flex items-center justify-between">
                      <span className="text-[11px] text-gray-500">{label}</span>
                      <span className="text-sm font-semibold text-gray-900">{value}</span>
                    </div>
                  ) : (
                    <div key={label}>
                      <div className="text-[11px] text-gray-500">{label}</div>
                      <div className="text-sm font-semibold text-gray-900">{value}</div>
                    </div>
                  )
                ))}
                <div className={isPopup ? 'flex items-center justify-between' : ''}>
                  <div className="text-[11px] text-gray-500">Aging</div>
                  {displayResult.aging_bucket
                    ? chip(AGING_BUCKET_BADGE_CLASS[displayResult.aging_bucket as AgingBucket] || 'bg-gray-100 text-gray-700', displayResult.aging_bucket)
                    : <div className="text-sm text-gray-400">—</div>}
                </div>
                {displayResult.cut_from_full && (
                  <div className={isPopup ? '' : 'col-span-2 sm:col-span-4'}>
                    {chip('bg-[#fdeaea] text-[#b42318]', 'Cut from full')}
                  </div>
                )}
              </div>
            )}

            {/* Talking/Email segmented toggle — same two-state, equal-weight
                visual as the rest of this codebase's tab-like controls (no
                new visual language for a plain two-option switch). Default:
                Talking, so existing behavior is unchanged for anyone not
                touching this. */}
            <div className="inline-flex self-start rounded-lg border border-gray-200 bg-gray-50 p-0.5" role="tablist">
              {(['talking', 'email'] as Format[]).map((format) => (
                <button
                  key={format}
                  type="button"
                  role="tab"
                  aria-selected={activeFormat === format}
                  onClick={() => handleTabSwitch(format)}
                  className={`px-3.5 py-1 rounded-md text-xs font-semibold cursor-pointer capitalize transition-colors ${
                    activeFormat === format ? 'bg-white text-[#107c41] shadow-2xs' : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {format}
                </button>
              ))}
            </div>

            {(activeTab.status === 'idle' || activeTab.status === 'loading') && (
              <div className="animate-pulse flex flex-col gap-3">
                <div className="h-16 bg-gray-100 rounded-lg" />
                <div className="h-32 bg-gray-100 rounded-lg" />
              </div>
            )}

            {activeTab.status === 'error' && (
              <p className="text-sm text-gray-400">ERROR: {activeTab.error}</p>
            )}

            {activeTab.status === 'result' && activeTab.result && (
              <>
                <div className="relative">
                  <pre className="text-sm whitespace-pre-wrap bg-white border border-gray-200 rounded-lg p-4 leading-relaxed">{activeTab.result.script_text}</pre>
                </div>

                <div className={isPopup ? 'flex flex-col gap-2' : 'flex items-center gap-2'}>
                  <button
                    type="button"
                    onClick={handleCopy}
                    className={`flex items-center justify-center gap-1.5 px-3 py-1.5 border border-gray-200 rounded-lg text-xs font-semibold text-gray-700 hover:bg-gray-50 cursor-pointer ${isPopup ? 'w-full' : ''}`}
                  >
                    <Copy className="w-3.5 h-3.5" /> {copied ? 'Copied!' : 'Copy'}
                  </button>
                  <button
                    type="button"
                    onClick={handleRegenerate}
                    className={`flex items-center justify-center gap-1.5 px-3 py-1.5 bg-[#107c41] hover:bg-[#0d6535] text-white rounded-lg text-xs font-bold cursor-pointer ${isPopup ? 'w-full' : ''}`}
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> Regenerate
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
