import { api } from './client';
import { AuditLogListResult, AuditLogQuery } from '../types';

// GET /audit-log (backend/api/routers/audit_log.py) — vendor-joined,
// filterable (search/source/date range), paginated (limit/offset).
// Returns {items, total}, most-recent-first.
export const getAuditLog = (query: AuditLogQuery = {}) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  const qs = params.toString();
  return api.get<AuditLogListResult>(`/audit-log${qs ? `?${qs}` : ''}`);
};
