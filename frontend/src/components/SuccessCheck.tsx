import React, { useEffect } from 'react';
import { Check } from 'lucide-react';

interface SuccessCheckProps {
  message?: string;
  durationMs?: number;
  onDone: () => void;
}

// Brief full-surface green checkmark takeover (UPI-app style) shown right
// after an action succeeds — auto-dismisses itself via onDone, the caller
// decides what "done" means (close the modal, etc).
export const SuccessCheck: React.FC<SuccessCheckProps> = ({ message, durationMs = 1300, onDone }) => {
  useEffect(() => {
    const timer = setTimeout(onDone, durationMs);
    return () => clearTimeout(timer);
  }, [durationMs, onDone]);

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-white/95 rounded-2xl">
      <div className="flex flex-col items-center gap-3 animate-in zoom-in-75 fade-in duration-300">
        <div className="w-16 h-16 rounded-full bg-[#107c41] flex items-center justify-center shadow-lg shadow-emerald-200">
          <Check className="w-9 h-9 text-white" strokeWidth={3} />
        </div>
        {message && <p className="text-sm font-semibold text-gray-800 text-center max-w-xs">{message}</p>}
      </div>
    </div>
  );
};
