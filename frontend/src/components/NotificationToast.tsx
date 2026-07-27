import React, { useEffect } from 'react';
import { AlertTriangle, CheckCircle2, X, XCircle } from 'lucide-react';

export type ToastVariant = 'success' | 'error' | 'warning';

interface NotificationToastProps {
  message: string | null;
  variant: ToastVariant;
  onClose: () => void;
}

const TOAST_DURATION_MS = 4000;

// One popup surface for every fire-and-forget action result app-wide
// (generate/regenerate/finalize/upload/revert/config-edit/payment) —
// bottom-right, matching test_ui.html's green-card convention, with a
// red/amber variant so an error never wears the green "success" look.
const VARIANT_STYLE: Record<ToastVariant, { bg: string; border: string; icon: React.ReactNode }> = {
  success: { bg: 'bg-[#107c41]', border: 'border-emerald-500/20', icon: <CheckCircle2 className="w-4 h-4 text-emerald-300 shrink-0" /> },
  error: { bg: 'bg-[#b42318]', border: 'border-red-500/20', icon: <XCircle className="w-4 h-4 text-red-200 shrink-0" /> },
  warning: { bg: 'bg-[#b56a00]', border: 'border-amber-500/20', icon: <AlertTriangle className="w-4 h-4 text-amber-200 shrink-0" /> },
};

export const NotificationToast: React.FC<NotificationToastProps> = ({
  message,
  variant,
  onClose,
}) => {
  useEffect(() => {
    if (message) {
      const timer = setTimeout(() => {
        onClose();
      }, TOAST_DURATION_MS);
      return () => clearTimeout(timer);
    }
  }, [message, onClose]);

  if (!message) return null;
  const style = VARIANT_STYLE[variant];

  return (
    <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-3 ${style.bg} text-white px-4 py-3 rounded-xl shadow-lg border ${style.border} animate-in slide-in-from-bottom-5 duration-300 max-w-md`}>
      {style.icon}
      <span className="text-xs font-medium leading-tight">{message}</span>
      <button
        onClick={onClose}
        className="p-1 text-gray-300 hover:text-white rounded-md transition-colors cursor-pointer ml-auto"
        aria-label="Dismiss notification"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
};
