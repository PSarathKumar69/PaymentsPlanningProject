import { api } from './client';
import { ExtraField, MasterDataCommitResult, MasterDataRevertResult, MasterGrid } from '../types';

// One POST, now gated behind the upload confirm modal (Main-tab
// upload-confirm task) rather than firing immediately on file pick/drop.
// planningMonth ("YYYY-MM"): Finance's confirmed planning month for this
// cycle, from that modal — optional so any caller that omits it leaves the
// persisted value untouched. sheetStartMonth ("YYYY-MM"): still a real,
// optional server-side override (P1 demo-readiness task) — the confirm
// modal no longer surfaces it (Sarath's call, kept the card minimal), so
// every current caller omits it and the backend just uses whatever's
// currently configured.
export const commitUpload = (file: File, planningMonth?: string, sheetStartMonth?: string) =>
  api.upload<MasterDataCommitResult>('/master-data/commit-upload', file, {
    ...(planningMonth ? { planning_month: planningMonth } : {}),
    ...(sheetStartMonth ? { sheet_start_month: sheetStartMonth } : {}),
  });

// Restores the ONE backup slot the backend keeps (commit_upload() overwrites
// it every time, never versioned/timestamped) — there is no endpoint for an
// arbitrary multi-entry upload history; see the report handed back to Sarath.
export const revertUpload = () => api.post<MasterDataRevertResult>('/master-data/revert');

export const getMasterGrid = () => api.get<MasterGrid>('/master-data/grid');
export const getExtraFields = () => api.get<ExtraField[]>('/master-data/extra-fields');

export const patchExtraField = (vendorId: number, columnName: string, newValue: string | null) =>
  api.patch<{ old_value: string | null; new_value: string | null }>(`/master-data/extra-fields/${vendorId}`, {
    column_name: columnName,
    new_value: newValue,
  });
