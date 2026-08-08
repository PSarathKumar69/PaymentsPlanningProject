import React from 'react';
import {
  Database,
  LayoutGrid,
  BarChart3,
  Settings,
  ChevronsLeft,
  ChevronsRight,
  LogOut,
} from 'lucide-react';
import { NavItemKey } from '../types';
import { PLACEHOLDER_CURRENT_USER } from '../constants/currentUser';

interface SidebarProps {
  isCollapsed: boolean;
  setIsCollapsed: (value: boolean | ((prev: boolean) => boolean)) => void;
  activeNav: NavItemKey;
  setActiveNav: (nav: NavItemKey) => void;
  onLogoutClick?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  isCollapsed,
  setIsCollapsed,
  activeNav,
  setActiveNav,
  onLogoutClick,
}) => {
  const navItems: { key: NavItemKey; label: string; icon: React.ReactNode }[] = [
    {
      key: 'main',
      label: 'Main',
      icon: <Database className="w-4 h-4 shrink-0" />,
    },
    {
      key: 'planning',
      label: 'Planning',
      icon: <LayoutGrid className="w-4 h-4 shrink-0" />,
    },
    {
      key: 'analytics',
      label: 'Analytics',
      icon: <BarChart3 className="w-4 h-4 shrink-0" />,
    },
    {
      key: 'configuration',
      label: 'Configuration',
      icon: <Settings className="w-4 h-4 shrink-0" />,
    },
  ];

  return (
    <aside
      className={`relative bg-white border border-gray-200 rounded-2xl shadow-2xs overflow-hidden flex flex-col justify-between transition-all duration-300 ease-in-out shrink-0 select-none h-full z-20 ${
        isCollapsed ? 'w-[68px]' : 'w-[200px]'
      }`}
      aria-label="Sidebar navigation"
    >
      {/* Top Header & Brand */}
      <div className="p-4 flex flex-col gap-6">
        <div className="flex items-center justify-between h-7">
          {!isCollapsed && (
            <span className="text-sm font-bold text-[#107c41] tracking-tight truncate flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-[#107c41]"></span>
              Vendor Payments
            </span>
          )}

          <button
            onClick={() => setIsCollapsed((prev) => !prev)}
            className={`p-1.5 text-gray-400 hover:text-[#107c41] hover:bg-emerald-50/60 rounded-lg transition-colors ${
              isCollapsed ? 'mx-auto' : ''
            }`}
            title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {isCollapsed ? (
              <ChevronsRight className="w-4 h-4" />
            ) : (
              <ChevronsLeft className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* Stacked Navigation Buttons */}
        <nav className="flex flex-col gap-1.5" aria-label="Main menu">
          {navItems.map((item) => {
            const isActive = activeNav === item.key;
            return (
              <button
                key={item.key}
                onClick={() => setActiveNav(item.key)}
                title={isCollapsed ? item.label : undefined}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold transition-all ${
                  isActive
                    ? 'bg-[#107c41] text-white shadow-xs'
                    : 'text-gray-600 hover:bg-emerald-50/50 hover:text-[#107c41]'
                } ${isCollapsed ? 'justify-center px-0' : 'justify-start'}`}
              >
                <span className={isActive ? 'text-white' : 'text-gray-500'}>
                  {item.icon}
                </span>
                {!isCollapsed && <span className="truncate">{item.label}</span>}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Bottom Profile Section */}
      <div className="p-3 border-t border-gray-100 bg-white">
        <div
          className={`flex items-center ${
            isCollapsed ? 'justify-center' : 'justify-between'
          }`}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            {/* Circular Avatar */}
            <div className="w-8 h-8 rounded-full bg-[#dcfce7] text-[#107c41] font-bold text-xs flex items-center justify-center shrink-0 border border-[#bbf7d0]">
              {PLACEHOLDER_CURRENT_USER.initials}
            </div>

            {!isCollapsed && (
              <div className="flex flex-col min-w-0 pr-1">
                <span className="text-xs font-semibold text-gray-900 truncate leading-tight">
                  {PLACEHOLDER_CURRENT_USER.name}
                </span>
                <span className="text-[11px] text-gray-500 truncate leading-tight">
                  {PLACEHOLDER_CURRENT_USER.role}
                </span>
              </div>
            )}
          </div>

          {!isCollapsed && (
            <button
              onClick={onLogoutClick}
              className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors shrink-0"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </aside>
  );
};
