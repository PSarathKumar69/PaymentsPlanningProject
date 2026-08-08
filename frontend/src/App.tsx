import React, { useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { MainTab } from './components/MainTab';
import { PlanningView } from './components/PlanningView';
import { ConfigurationTab } from './components/ConfigurationTab';
import { NotificationToast, ToastVariant } from './components/NotificationToast';
import VendorAnalyticsTab from './components/VendorAnalyticsTab';
import { NavItemKey } from './types';
import { PLACEHOLDER_CURRENT_USER } from './constants/currentUser';

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

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#eef2ef] font-['Inter',system-ui,sans-serif] p-3 gap-3">
      <Sidebar
        isCollapsed={isSidebarCollapsed}
        setIsCollapsed={setIsSidebarCollapsed}
        activeNav={activeNav}
        setActiveNav={setActiveNav}
        onLogoutClick={() => notify(`Logged out ${PLACEHOLDER_CURRENT_USER.name} (Demo).`)}
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
