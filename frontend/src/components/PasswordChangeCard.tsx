import React, { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { changePassword } from '../api/auth';
import { friendlyErrorMessage } from '../api/client';
import { ToastVariant } from './NotificationToast';

interface PasswordChangeCardProps {
  onNotify?: (message: string, variant?: ToastVariant) => void;
}

const MIN_PASSWORD_LENGTH = 6;

// Login-credential task: replaces the login page's old "Forgot Password?"
// link (which never did more than show a message — there's no email/SMS
// flow to prove who's asking). This is the real, self-service option:
// prove identity with your CURRENT password, same rule the backend route
// enforces (backend/api/routers/auth.py's change_password()).
export const PasswordChangeCard: React.FC<PasswordChangeCardProps> = ({ onNotify }) => {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');

  const inputCls =
    'w-full text-xs border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#107c41]/20 focus:border-[#107c41] transition-colors';

  const reset = () => {
    setCurrentPassword('');
    setNewPassword('');
    setConfirmPassword('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!currentPassword || !newPassword || !confirmPassword) {
      setError('Fill in all three fields.');
      return;
    }
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`New password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('New password and confirmation don’t match.');
      return;
    }

    setIsSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      reset();
      onNotify?.('Password updated.', 'success');
    } catch (err) {
      // Same message the backend actually returns for a wrong current
      // password ("Current password is incorrect.") — shown as-is, it's
      // already clear and specific.
      setError(friendlyErrorMessage(err, 'Could not update your password — please try again.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white border border-gray-200/90 rounded-xl p-5 shadow-xs flex flex-col gap-3 w-full"
    >
      <div className="flex items-center gap-2.5 pb-2 border-b border-gray-100">
        <div className="w-7 h-7 rounded-full bg-emerald-50 text-[#107c41] border border-emerald-200/80 flex items-center justify-center shrink-0">
          <KeyRound className="w-3.5 h-3.5" />
        </div>
        <h3 className="text-sm font-bold text-gray-900">Change password</h3>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-semibold text-gray-500">Current password</label>
          <input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            className={inputCls}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-semibold text-gray-500">New password</label>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
            className={inputCls}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-semibold text-gray-500">Confirm new password</label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            className={inputCls}
          />
        </div>
      </div>

      {error && <p className="text-xs font-medium text-[#b42318]">{error}</p>}

      <div>
        <button
          type="submit"
          disabled={isSubmitting}
          className="px-3 py-1.5 bg-[#107c41] hover:bg-[#0d6535] disabled:opacity-60 disabled:cursor-not-allowed text-white rounded-lg text-xs font-bold cursor-pointer"
        >
          {isSubmitting ? 'Updating…' : 'Reset Password'}
        </button>
      </div>
    </form>
  );
};

export default PasswordChangeCard;
