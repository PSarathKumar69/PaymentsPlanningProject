// GET /models/5/finalized-plan-export returns a binary .xlsx, not JSON —
// client.ts's request()/api.get() always calls res.json(), so it can't be
// reused as-is for this one endpoint. Small standalone helper instead of a
// second wrapper class: same res.ok/detail-parsing error shape as
// client.ts's request() (matching toast experience on failure), returning
// res.blob() on success.
import { ApiError, parseContentDispositionFilename } from './client';

export async function downloadFinalizedPlanExport(): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch('/models/5/finalized-plan-export');
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : res.statusText;
    throw new ApiError(detail);
  }
  const filename = parseContentDispositionFilename(res.headers.get('Content-Disposition'));
  return { blob: await res.blob(), filename };
}

export async function downloadMinFundsVerificationExport(): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch('/models/5/min-funds-verification-export');
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : res.statusText;
    throw new ApiError(detail);
  }
  const filename = parseContentDispositionFilename(res.headers.get('Content-Disposition'));
  return { blob: await res.blob(), filename };
}
