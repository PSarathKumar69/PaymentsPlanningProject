import React, { useState } from 'react';
import { KeyRound, UserCircle2 } from 'lucide-react';
import { login } from '../api/auth';
import { friendlyErrorMessage } from '../api/client';
import { CurrentUser } from '../api/auth';

interface LoginPageProps {
  onLoginSuccess: (user: CurrentUser) => void;
}

// Login-credential task: a small, fixed allowlist of named Finance users
// (backend/db/seed_users.py), not open sign-up — same layout as the
// reference mockup (icon badge, centered card, username/password with
// inline icons, remember-me row, full-width button), recolored from the
// mockup's pink-to-teal gradient to this app's own Excel-green/white-grid
// theme (bg-excel-grid utility + #107c41, same as Sidebar.tsx/index.css)
// instead of introducing a second, one-off palette.
//
// No "Forgot Password?" link (removed per Sarath's request) — there's no
// email/SMS flow to prove who's asking, so a real self-service reset isn't
// possible here. The actual self-service option now lives on the
// Configuration page instead: an already-logged-in user can change their
// own password there (ConfigurationTab.tsx's PasswordChangeCard), proving
// identity with their current password rather than a "forgot" link that
// only ever showed a message anyway.
export const LoginPage: React.FC<LoginPageProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError('Enter both a username and a password.');
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      const user = await login(username.trim(), password, remember);
      onLoginSuccess(user);
    } catch (err) {
      // Deliberately the same message for "wrong password" and "unknown
      // username" (backend/api/routers/auth.py) — shown as-is here too, so
      // the UI never gives away which of the two it was.
      setError(friendlyErrorMessage(err, 'Could not sign in — please try again.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen w-screen flex items-center justify-center relative overflow-hidden bg-gradient-to-br from-white via-[#eef7f0] to-[#d7f2e3] p-4">
      <div className="absolute inset-0 bg-excel-grid opacity-[0.85] pointer-events-none" />

      <form
        onSubmit={handleSubmit}
        className="relative z-10 w-full max-w-sm bg-white border border-gray-200/90 rounded-2xl shadow-xl p-8 flex flex-col items-center gap-5"
      >
        <div className="w-16 h-16 rounded-full bg-[#107c41] flex items-center justify-center shadow-md">
          <UserCircle2 className="w-9 h-9 text-white" strokeWidth={1.5} />
        </div>

        <div className="flex flex-col items-center gap-1">
          <h1 className="text-lg font-bold text-gray-900 tracking-tight">User Login</h1>
        </div>

        <div className="w-full flex flex-col gap-3">
          <label className="w-full flex items-center gap-2.5 border border-gray-200 rounded-lg px-3 py-2.5 focus-within:ring-2 focus-within:ring-[#107c41]/20 focus-within:border-[#107c41] transition-colors">
            <UserCircle2 className="w-4 h-4 text-gray-400 shrink-0" />
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              autoComplete="username"
              autoFocus
              className="w-full text-sm text-gray-900 placeholder:text-gray-400 outline-none"
            />
          </label>

          <label className="w-full flex items-center gap-2.5 border border-gray-200 rounded-lg px-3 py-2.5 focus-within:ring-2 focus-within:ring-[#107c41]/20 focus-within:border-[#107c41] transition-colors">
            <KeyRound className="w-4 h-4 text-gray-400 shrink-0" />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoComplete="current-password"
              className="w-full text-sm text-gray-900 placeholder:text-gray-400 outline-none"
            />
          </label>
        </div>

        {error && <p className="w-full text-xs font-medium text-[#b42318] -mt-1">{error}</p>}

        <div className="w-full flex items-center text-xs">
          <label className="flex items-center gap-1.5 text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="accent-[#107c41] cursor-pointer"
            />
            Remember me
          </label>
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full py-2.5 bg-[#107c41] hover:bg-[#0d6535] disabled:opacity-60 disabled:cursor-not-allowed text-white rounded-lg text-sm font-bold tracking-wide transition-colors cursor-pointer"
        >
          {isSubmitting ? 'Signing in…' : 'LOGIN'}
        </button>
      </form>
    </div>
  );
};

export default LoginPage;
