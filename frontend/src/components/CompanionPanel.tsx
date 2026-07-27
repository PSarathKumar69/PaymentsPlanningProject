import React, { useState } from 'react';
import { MessageCircle, X } from 'lucide-react';
import { postVendorTalkingPoints } from '../api/aiCompanion';
import { ApiError } from '../api/client';
import { Vendor } from '../types';

interface CompanionPanelProps {
  planVendors: Vendor[]; // vendors in THIS cycle's New Model 2 plan only (docs/14)
}

type Step = 'pick' | 'loading' | 'result' | 'error';

const MAX_COMPANION_MATCHES = 8;

// Guided "pick a vendor -> auto-generate" flow (New Model 2 tab only) — no
// free-text chat, deliberately removed upstream. Session-only, lost on
// reload (same reasonable-default test_ui.html documents).
export const CompanionPanel: React.FC<CompanionPanelProps> = ({ planVendors }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [step, setStep] = useState<Step>('pick');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Vendor | null>(null);
  const [result, setResult] = useState('');
  const [error, setError] = useState('');

  const toggle = () => {
    const opening = !isOpen;
    setIsOpen(opening);
    if (opening) {
      setStep('pick');
      setSelected(null);
      setQuery('');
    }
  };

  const pick = async (vendor: Vendor) => {
    setSelected(vendor);
    setStep('loading');
    try {
      const resp = await postVendorTalkingPoints(vendor.id);
      setResult(resp.script_text);
      setStep('result');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setStep('error');
    }
  };

  const startOver = () => {
    setStep('pick');
    setSelected(null);
    setQuery('');
  };

  const matches = query.trim()
    ? planVendors
        .filter((v) => v.vendor_name.toLowerCase().includes(query.trim().toLowerCase()) || v.erp_code.toLowerCase().includes(query.trim().toLowerCase()))
        .slice(0, MAX_COMPANION_MATCHES)
    : [];

  return (
    <>
      <button
        type="button"
        onClick={toggle}
        title="Vendor talking points"
        aria-label="Open vendor talking points"
        className="fixed bottom-6 right-6 w-14 h-14 rounded-full bg-[#107c41] hover:bg-[#0d6535] text-white shadow-lg flex items-center justify-center z-40 cursor-pointer"
      >
        <MessageCircle className="w-6 h-6" />
      </button>

      {isOpen && (
        <div className="fixed bottom-24 right-6 w-80 max-h-[460px] bg-white border border-gray-200 rounded-xl shadow-2xl flex flex-col z-40 overflow-hidden">
          <div className="px-3.5 py-2.5 bg-emerald-50 text-[#107c41] font-bold text-sm flex items-center justify-between">
            <span>Vendor talking points</span>
            <button onClick={toggle} aria-label="Close" className="cursor-pointer">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto no-scrollbar p-3 flex flex-col gap-2">
            {step === 'loading' && (
              <p className="text-xs text-gray-400">Generating talking points for {selected?.vendor_name}…</p>
            )}
            {step === 'result' && (
              <>
                <button onClick={startOver} className="text-xs text-[#107c41] font-semibold cursor-pointer self-start">&larr; Back</button>
                <h3 className="text-sm font-bold text-gray-900">{selected?.vendor_name} ({selected?.erp_code})</h3>
                <p className="text-xs whitespace-pre-wrap">{result}</p>
              </>
            )}
            {step === 'error' && (
              <>
                <button onClick={startOver} className="text-xs text-[#107c41] font-semibold cursor-pointer self-start">&larr; Back</button>
                <p className="text-xs text-gray-400">ERROR: {error}</p>
              </>
            )}
            {step === 'pick' && (
              planVendors.length === 0 ? (
                <p className="text-xs text-gray-400">Generate a New Model 2 plan first, then come back for talking points.</p>
              ) : (
                <>
                  <p className="text-xs text-gray-500">Which vendor do you want talking points for?</p>
                  <input
                    autoFocus
                    type="text"
                    placeholder="Vendor name or ERP code…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs"
                  />
                  <div className="flex flex-col gap-1">
                    {matches.map((v) => (
                      <button
                        key={v.id}
                        onClick={() => pick(v)}
                        className="text-left text-xs px-2.5 py-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 cursor-pointer"
                      >
                        {v.vendor_name} ({v.erp_code})
                      </button>
                    ))}
                    {query.trim() && matches.length === 0 && <p className="text-xs text-gray-400">No matching vendor.</p>}
                  </div>
                </>
              )
            )}
          </div>
        </div>
      )}
    </>
  );
};
