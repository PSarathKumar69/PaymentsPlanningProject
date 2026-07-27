import React, { useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { MainTab } from './components/MainTab';
import { PlanningView } from './components/PlanningView';
import { ConfigurationTab } from './components/ConfigurationTab';
import { NotificationToast, ToastVariant } from './components/NotificationToast';
import { NavItemKey } from './types';
import { PLACEHOLDER_CURRENT_USER } from './constants/currentUser';
import { BarChart3 } from 'lucide-react';

export default function App() {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [activeNav, setActiveNav] = useState<NavItemKey>('planning');
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [toastVariant, setToastVariant] = useState<ToastVariant>('success');
  const notify = (msg: string, variant: ToastVariant = 'success') => {
    setToastVariant(variant);
    setToastMessage(msg);
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#eef2ef] font-['Inter',system-ui,sans-serif]">
      <Sidebar
        isCollapsed={isSidebarCollapsed}
        setIsCollapsed={setIsSidebarCollapsed}
        activeNav={activeNav}
        setActiveNav={setActiveNav}
        onLogoutClick={() => notify(`Logged out ${PLACEHOLDER_CURRENT_USER.name} (Demo).`)}
      />

      <main className="flex-1 flex flex-col h-full bg-[#f0f4f1] relative overflow-y-auto no-scrollbar px-6 md:px-12 py-4">
        <div className="absolute inset-0 bg-excel-grid opacity-[0.85] pointer-events-none z-0" />

        <div className="relative z-10 min-h-full flex flex-col">
          {activeNav === 'main' && <MainTab onNotify={notify} onUploaded={() => setActiveNav('planning')} />}
          {activeNav === 'planning' && <PlanningView onNotify={notify} />}
          {activeNav === 'configuration' && <ConfigurationTab onNotify={notify} />}
          {activeNav === 'analytics' && (
            /* Placeholder — Sarath will provide a reference for this tab later (docs/15). */
            <div className="flex flex-col h-full w-full justify-center items-center max-w-3xl mx-auto text-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-emerald-50 text-[#107c41] flex items-center justify-center border border-emerald-200 shadow-2xs">
                <BarChart3 className="w-6 h-6" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900">Analytics</h2>
                <p className="text-xs text-gray-500 mt-1 max-w-md">
                  Placeholder — not built yet. Sarath will provide a reference for what this tab shows.
                </p>
              </div>
              <button
                onClick={() => setActiveNav('planning')}
                className="mt-2 px-4 py-2 bg-[#107c41] text-white rounded-lg text-xs font-semibold hover:bg-[#0d6535] transition-colors cursor-pointer"
              >
                Return to Planning View
              </button>
            </div>
          )}
        </div>
      </main>

      <NotificationToast message={toastMessage} variant={toastVariant} onClose={() => setToastMessage(null)} />
    </div>
  );
}
