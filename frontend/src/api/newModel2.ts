import { api } from './client';
import {
  AllVendorMinFundsRequired,
  CurrentPlanningMonth,
  FinalizeCheckResponse,
  NewModel2MinimumFundsRequired,
  NewModel2PlanAndWeeklyView,
  PlanRunHistory,
  ResetCycleResponse,
  SuggestedPlanningMonth,
  VendorMinFundsRequired,
} from '../types';

export const getSuggestedPlanningMonth = () => api.get<SuggestedPlanningMonth>('/models/5/suggested-planning-month');

// Whatever _resolve_planning_month() would fall back to right now — the
// current cycle's already-confirmed planning month, read-only, no plan_run
// required. Used by the Main-tab upload confirm modal (pre-fill) and the
// Planning tab (read-only display, no re-ask).
export const getCurrentPlanningMonth = () => api.get<CurrentPlanningMonth>('/models/5/current-planning-month');

export const getPlanRuns = () => api.get<PlanRunHistory>('/models/5/plan-runs');

export const getMinimumFundsRequired = (planningMonth: string) =>
  api.get<NewModel2MinimumFundsRequired>(`/models/5/minimum-funds-required?planning_month=${planningMonth}`);

export const getVendorMinFundsRequired = (vendorId: number, planningMonth?: string) =>
  api.get<VendorMinFundsRequired>(
    `/models/5/vendors/${vendorId}/min-funds-required${planningMonth ? `?planning_month=${planningMonth}` : ''}`
  );

export const getAllVendorMinFundsRequired = (planningMonth?: string) =>
  api.get<AllVendorMinFundsRequired>(
    `/models/5/vendor-min-funds-required${planningMonth ? `?planning_month=${planningMonth}` : ''}`
  );

export const generatePlanAndWeeklyView = (availableFunds: number, planningMonth?: string) =>
  api.post<NewModel2PlanAndWeeklyView>('/models/5/generate-plan-and-weekly-view', {
    available_funds: availableFunds,
    ...(planningMonth ? { planning_month: planningMonth } : {}),
  });

export const finalizeNewModel2 = () => api.post<FinalizeCheckResponse>('/models/5/finalize');

// Reset button — clears the current planning cycle's calculations only
// (this cycle's PlanRun/PlanAllocation rows + every vendor's whole-month
// cycle state). Never touches vendor master data/Excel, Configuration,
// past months' plan history, or the confirmed planning month itself.
export const resetNewModel2Cycle = () => api.post<ResetCycleResponse>('/models/5/reset-cycle');
