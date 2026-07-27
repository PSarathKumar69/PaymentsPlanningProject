import { api } from './client';

export interface DeletePlanRunResult {
  plan_run_id: number;
  deleted_allocations: number;
  cleared_override_vendor_ids: number[];
}

export const deletePlanRun = (planRunId: number) =>
  api.delete<DeletePlanRunResult>(`/plan-runs/${planRunId}`);
