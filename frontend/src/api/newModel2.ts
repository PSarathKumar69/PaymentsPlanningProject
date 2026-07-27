import { api } from './client';
import {
  AllVendorMinFundsRequired,
  FinalizeCheckResponse,
  NewModel2MinimumFundsRequired,
  NewModel2PlanAndWeeklyView,
  PlanRunHistory,
  VendorMinFundsRequired,
} from '../types';

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
