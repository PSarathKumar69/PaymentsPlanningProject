import { api } from './client';
import { PriorityBucket } from '../types';

// The Priority Tag dropdown on the Planning table reads THIS list at
// runtime (CLAUDE.md rule 7) — never a hardcoded ["P2","P3","P4","P5"]
// array. Adding/removing a bucket here is meant to change what's
// selectable there with zero frontend code changes.
export const getPriorityBuckets = () => api.get<PriorityBucket[]>('/config/priority-buckets');

export const addPriorityBucket = (bucket: PriorityBucket) =>
  api.post<PriorityBucket>('/config/priority-buckets', bucket);

export const updatePriorityBucket = (
  bucketKey: string,
  patch: Partial<Pick<PriorityBucket, 'display_label' | 'ceiling_pct' | 'floor_pct' | 'rotation_position'>>
) => api.put<{ changed: boolean }>(`/config/priority-buckets/${bucketKey}`, patch);

export const removePriorityBucket = (bucketKey: string) =>
  api.delete<{ removed: string }>(`/config/priority-buckets/${bucketKey}`);
