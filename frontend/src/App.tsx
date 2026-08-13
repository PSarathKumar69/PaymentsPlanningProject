import React, { useEffect, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { MainTab } from './components/MainTab';
import { PlanningView } from './components/PlanningView';
import { ConfigurationTab } from './components/ConfigurationTab';
import { LoginPage } from './components/LoginPage';
import { NotificationToast, ToastVariant } from './components/NotificationToast';
import VendorAnalyticsTab from './components/VendorAnalyticsTab';
import { NavItemKey } from './types';
import { CurrentUser, fetchCurrentUser, logout } from './api/auth';

export default function App() {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [activeNav, setActiveNav] = useState<NavItemKey>('planning');
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [toastVariant, setToastVariant] = useState<ToastVariant>('success');
  // Bug fix: PlanningView stays mounted (hidden via CSS, never unmounted —
  // see below) across a tab switch, so its own mount-effect never re-fires
  // after an upload/rollover. Bumping this forces it to refetch, same
  // refreshSignal convention MasterDataGrid.tsx already uses.
  const [refreshSignal, setRefreshSignal] = useState(0);
  const notify = (msg: string, variant: ToastVariant = 'success') => {
    setToastVariant(variant);
    setToastMessage(msg);
  };

  // Login-credential task: gate the whole app behind a real session
  // instead of the old PLACEHOLDER_CURRENT_USER stand-in. `authChecked`
  // stays false only for the one initial /auth/me round trip, so a page
  // reload never flashes the login page before we actually know whether
  // the existing cookie is still valid.
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    fetchCurrentUser().then((user) => {
      setCurrentUser(user);
      setAuthChecked(true);
    });
  }, []);

  // A session that expires (or gets logged out in another tab) mid-use
  // surfaces as a 401 on whatever the user does next — api/client.ts
  // broadcasts that instead of leaving the app stuck retrying against a
  // dead cookie.
  useEffect(() => {
    const onUnauthorized = () => setCurrentUser(null);
    window.addEventListener('auth:unauthorized', onUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized);
  }, []);

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      setCurrentUser(null);
    }
  };

  if (!authChecked) {
    return <div className="min-h-screen w-screen bg-[#eef2ef]" />;
  }

  if (!currentUser) {
    return <LoginPage onLoginSuccess={setCurrentUser} />;
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#eef2ef] font-['Inter',system-ui,sans-serif] p-3 gap-3">
      <Sidebar
        isCollapsed={isSidebarCollapsed}
        setIsCollapsed={setIsSidebarCollapsed}
        activeNav={activeNav}
        setActiveNav={setActiveNav}
        currentUser={currentUser}
        onLogoutClick={handleLogout}
      />

      <main className="flex-1 flex flex-col h-full bg-[#f0f4f1] rounded-2xl relative overflow-y-auto thin-scrollbar overscroll-contain px-6 md:px-12 py-4">
        <div className="absolute inset-0 bg-excel-grid opacity-[0.85] pointer-events-none z-0" />

        {/* Every tab stays mounted (hidden via CSS, not conditionally
            rendered) so switching tabs never wipes a tab's own local state
            — e.g. MainTab's session-only upload history, which used to be
            destroyed the instant onUploaded() navigated away to Planning
            right after a successful upload. */}
        <div className="relative z-10 min-h-full flex flex-col" style={{ display: activeNav === 'main' ? 'flex' : 'none' }}>
          <MainTab onNotify={notify} onUploaded={() => { setActiveNav('planning'); setRefreshSignal((n) => n + 1); }} />
        </div>
        <div className="relative z-10 min-h-full flex flex-col" style={{ display: activeNav === 'planning' ? 'flex' : 'none' }}>
          <PlanningView onNotify={notify} refreshSignal={refreshSignal} />
        </div>
        <div className="relative z-10 min-h-full flex flex-col" style={{ display: activeNav === 'configuration' ? 'flex' : 'none' }}>
          <ConfigurationTab onNotify={notify} />
        </div>
        <div className="relative z-10 min-h-full flex flex-col" style={{ display: activeNav === 'analytics' ? 'flex' : 'none' }}>
          <VendorAnalyticsTab refreshSignal={refreshSignal} />
        </div>
      </main>

      <NotificationToast message={toastMessage} variant={toastVariant} onClose={() => setToastMessage(null)} />
    </div>
  );
}
