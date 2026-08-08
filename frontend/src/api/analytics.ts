import { api, ApiError, parseContentDispositionFilename } from './client';
import { AnalyticsDashboard, FundsTrend } from '../types';

export const getAnalyticsDashboard = () => api.get<AnalyticsDashboard>('/analytics/dashboard');

export const getAnalyticsFundsTrend = () => api.get<FundsTrend>('/analytics/funds-trend');

// GET /analytics/export returns a binary .xlsx, not JSON — same standalone
// pattern as api/planExport.ts's own download helpers (client.ts's
// request()/api.get() always calls res.json(), so it can't be reused here).
export async function downloadAnalyticsExport(): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch('/analytics/export');
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : res.statusText;
    throw new ApiError(detail);
  }
  const filename = parseContentDispositionFilename(res.headers.get('Content-Disposition'));
  return { blob: await res.blob(), filename };
}
