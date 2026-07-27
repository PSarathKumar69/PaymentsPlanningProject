import { api } from './client';
import { OverrideResult, WeekDistributionResult } from '../types';

export const patchOverride = (planAllocationId: number, overrideAmount: number | null) =>
  api.patch<OverrideResult>(`/plan-allocations/${planAllocationId}/override`, { override_amount: overrideAmount });

export const patchWeekDistribution = (planAllocationId: number, updates: Record<string, number>) =>
  api.patch<WeekDistributionResult>(`/plan-allocations/${planAllocationId}/week-distribution`, { updates });
