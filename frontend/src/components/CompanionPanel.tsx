import React, { useState } from 'react';
import { MessageCircle } from 'lucide-react';
import { Vendor } from '../types';
import { AiCompanionCard } from './AiCompanionCard';

interface CompanionPanelProps {
  planVendors: Vendor[]; // vendors in THIS cycle's New Model 2 plan only (docs/14)
}

// Floating FAB entry point (Planning tab only) — opens the shared
// AiCompanionCard (docs: AI screen revamp) with the vendor picker step,
// same guided "pick a vendor -> auto-generate" flow as before.
export const CompanionPanel: React.FC<CompanionPanelProps> = ({ planVendors }) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        title="Ask AI about a vendor"
        aria-label="Ask AI about a vendor"
        className="fixed bottom-6 right-6 w-14 h-14 rounded-full bg-[#107c41] hover:bg-[#0d6535] text-white shadow-lg flex items-center justify-center z-40 cursor-pointer"
      >
        <MessageCircle className="w-6 h-6" />
      </button>

      {isOpen && (
        <AiCompanionCard planVendors={planVendors} preselectedVendor={null} onClose={() => setIsOpen(false)} variant="popup" />
      )}
    </>
  );
};
