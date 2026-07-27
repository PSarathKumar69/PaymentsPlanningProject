import { api } from './client';
import { ExtraField, MasterDataCommitResult, MasterDataRevertResult, MasterGrid } from '../types';

// Single button, one file, one POST — takes effect immediately, no
// preview/confirm step (backend/ingestion/upload.py's own explicit design).
export const commitUpload = (file: File) =>
  api.upload<MasterDataCommitResult>('/master-data/commit-upload', file);

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
