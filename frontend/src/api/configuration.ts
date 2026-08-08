import { api } from './client';
import { PriorityBucket } from '../types';

// The Priority Tag dropdown on the Planning table reads THIS list at
// runtime (CLAUDE.md rule 7) — never a hardcoded ["P2","P3","P4","P5"]
// array. Adding/removing a bucket here is meant to change what's
// selectable there with zero frontend code changes.
export const getPriorityBuckets = () => api.get<PriorityBucket[]>('/config/priority-buckets');

export const addPriorityBucket = (bucket: Omit<PriorityBucket, 'deletable'>) =>
  api.post<PriorityBucket>('/config/priority-buckets', bucket);

export const updatePriorityBucket = (
  bucketKey: string,
  patch: Partial<Pick<PriorityBucket, 'display_label' | 'category_name' | 'ceiling_pct' | 'floor_pct' | 'rotation_position'>> & {
    new_bucket_key?: string;
  }
) => api.put<{ changed: boolean }>(`/config/priority-buckets/${bucketKey}`, patch);

export const removePriorityBucket = (bucketKey: string) =>
  api.delete<{ removed: string }>(`/config/priority-buckets/${bucketKey}`);

// Drag-to-reorder (Configuration-tab-rebuild task) — row position IS cut
// order now, Rotation Position is no longer its own editable field.
export const reorderPriorityBuckets = (orderedBucketKeys: string[]) =>
  api.put<{ changed: boolean }>('/config/priority-buckets/reorder', { ordered_bucket_keys: orderedBucketKeys });
