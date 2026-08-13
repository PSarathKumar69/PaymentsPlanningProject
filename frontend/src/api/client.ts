// Thin fetch wrapper — every component/hook in this app calls the API
// through the api/* modules built on this, never fetch() directly
// (docs/15: "swapping each mock import for a real api/* call should be a
// one-line change per component"). Mirrors test_ui.html's own apiCall():
// relative same-origin paths, JSON body/response, throws on a non-2xx
// response using the FastAPI error envelope's `detail` field.
//
// Vite's dev server proxies these relative paths to the real backend (see
// vite.config.ts) — the FastAPI app itself has no CORS middleware
// configured, so a direct cross-origin fetch from the Vite dev port would
// fail; the proxy keeps every request same-origin from the browser's POV,
// exactly like test_ui.html being served BY that same FastAPI app today.

export class ApiError extends Error {}

// Bug fix: a filename with spaces (every real download name here has them,
// e.g. "Vendor Payment Plan - Aug-2026 - ....xlsx") makes Starlette's
// FileResponse emit the RFC 5987 extended form (`filename*=utf-8''...`,
// percent-encoded) instead of a plain `filename="..."` — confirmed by
// reading starlette/responses.py's FileResponse.__init__, which switches
// forms whenever `urllib.parse.quote(filename) != filename` (true for any
// space). The old `/filename="?([^"]+)"?/` regex only ever matched the
// plain form, so it silently returned null on every one of these
// downloads, forever, and every caller fell back to its hardcoded literal
// name — which is why the browser kept appending "(1)", "(2)"... to the
// same literal filename on every repeat download.
export function parseContentDispositionFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const extended = disposition.match(/filename\*=(?:UTF-8|utf-8)''([^;]+)/);
  if (extended) {
    try {
      return decodeURIComponent(extended[1]);
    } catch {
      return extended[1];
    }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/);
  return plain ? plain[1] : null;
}

// Standard browser fetch reason phrases — exactly what `res.statusText`
// returns when the backend crashed with no real JSON `detail` body (an
// unhandled exception, not a raised HTTPException/ValueError with an
// actual human-authored message). A genuine backend-authored message would
// essentially never be byte-identical to one of these, so this is a safe,
// precise way to tell "real Finance-facing message" apart from "the
// backend just fell over" without touching how errors are caught/thrown.
const GENERIC_HTTP_REASON_PHRASES = new Set([
  'Bad Request', 'Unauthorized', 'Forbidden', 'Not Found', 'Method Not Allowed',
  'Conflict', 'Unprocessable Entity', 'Internal Server Error', 'Bad Gateway',
  'Service Unavailable', 'Gateway Timeout',
]);

// Finance-facing error text: a real ApiError with a real backend message
// (something a human wrote for this exact situation, e.g. "Can't remove a
// category still assigned to a vendor") is shown as-is. Anything else — a
// network failure, an unhandled backend crash, a raw JS exception string —
// is replaced with a short, plain fallback; the real error still goes to
// the console for debugging.
export function friendlyErrorMessage(e: unknown, fallback = 'Something went wrong — please try again.'): string {
  if (e instanceof ApiError && e.message && !GENERIC_HTTP_REASON_PHRASES.has(e.message)) {
    return e.message;
  }
  console.error(e);
  return fallback;
}

// FastAPI's own validation-error envelope puts `detail` as an ARRAY of
// {loc, msg, type} objects, not a string — passing that straight to
// `new Error(...)` stringifies it to the literally useless "[object
// Object]" (bug found this task: a stale backend process 404/422'd on
// /vendors/aging and the toast/red-banner just showed that instead of a
// readable message).
function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d))).join('; ');
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return fallback;
}

// Login-credential task: a session that expired (or got logged out in
// another tab) mid-use surfaces here as a 401 on whatever call happens
// next, same as it would on the very first request. Broadcasting it (App.tsx
// listens) instead of just throwing the ApiError lets the app snap back to
// the login page immediately, instead of leaving a logged-out user staring
// at a component that keeps failing every retry with the same 401.
function notifyUnauthorized(status: number, path: string) {
  if (status === 401 && path !== '/auth/login' && path !== '/auth/me') {
    window.dispatchEvent(new CustomEvent('auth:unauthorized'));
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = { method, headers: {} };
  if (body !== undefined) {
    (opts.headers as Record<string, string>)['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    notifyUnauthorized(res.status, path);
    throw new ApiError(detailToMessage(data.detail, res.statusText));
  }
  return data as T;
}

async function upload<T>(path: string, file: File, extraFields?: Record<string, string>): Promise<T> {
  const formData = new FormData();
  formData.append('file', file);
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) {
      formData.append(key, value);
    }
  }
  const res = await fetch(path, { method: 'POST', body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    notifyUnauthorized(res.status, path);
    throw new ApiError(detailToMessage(data.detail, res.statusText));
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, file: File, extraFields?: Record<string, string>) => upload<T>(path, file, extraFields),
};
