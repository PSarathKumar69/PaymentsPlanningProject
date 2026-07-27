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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = { method, headers: {} };
  if (body !== undefined) {
    (opts.headers as Record<string, string>)['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(detailToMessage(data.detail, res.statusText));
  }
  return data as T;
}

async function upload<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(path, { method: 'POST', body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
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
  upload: <T>(path: string, file: File) => upload<T>(path, file),
};
