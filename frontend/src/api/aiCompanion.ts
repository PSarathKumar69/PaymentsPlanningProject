import { api } from './client';
import { VendorTalkingPointsResult } from '../types';

// New Model 2's consolidated AI companion (docs: AI screen revamp) — any
// vendor status, backed by POST /ai/vendor-talking-points. Used by both
// AiCompanionCard entry paths (CompanionPanel's floating button and
// VendorDetailModal's "Ask AI about this vendor" link). The endpoint
// itself also returns the deterministic-layer fact pack (category,
// priority_tag, status, required/allocated amount, aging_bucket,
// cut_from_full) alongside script_text — nothing computed on the frontend.
export const postVendorTalkingPoints = (vendorId: number, format: 'talking' | 'email' = 'talking') =>
  api.post<VendorTalkingPointsResult>('/ai/vendor-talking-points', { vendor_id: vendorId, format });

// The older zero-status-only script — POST /ai/talking-scripts is kept
// backend-side for test_ui.html (the legacy reference dashboard, still
// present per CLAUDE.md), but the React app no longer calls it: the
// consolidated AiCompanionCard covers any status through the endpoint
// above, so this wrapper was removed as dead code.
