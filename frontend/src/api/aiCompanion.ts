import { api } from './client';
import { PlanAllocationRow, TalkingScript, VendorTalkingPointsResult } from '../types';

// New Model 2's own guided companion (tab 5 only) — pick a vendor, get
// talking points. No free-text chat (deliberately removed upstream).
export const postVendorTalkingPoints = (vendorId: number) =>
  api.post<VendorTalkingPointsResult>('/ai/vendor-talking-points', { vendor_id: vendorId });

// The older zero-status-only script — used by the vendor detail modal's
// "AI talking script" panel (present on every model tab including New
// Model 2's own, gated on the vendor's latest allocation status being
// "zero" — see PlanningView's VendorDetailModal). Distinct feature from the
// companion above; both are real and both are wired.
export const postTalkingScripts = (vendorAllocations: PlanAllocationRow[]) =>
  api.post<{ scripts: TalkingScript[] }>('/ai/talking-scripts', { vendor_allocations: vendorAllocations });
