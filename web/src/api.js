// Thin fetch wrapper around the dashboard JSON API.
//
// All requests are same-origin; the session cookie is set by /auth/telegram,
// so `credentials: 'same-origin'` is enough. A 401 (no session / expired
// cookie) is surfaced as an ApiError with `unauthorized === true` so the App
// can flip back to the login screen.

export class ApiError extends Error {
  constructor(status, detail, unauthorized = false) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.unauthorized = unauthorized;
  }
}

export function isApiError(err) {
  return err instanceof ApiError;
}

async function request(path, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  let res;
  try {
    res = await fetch(path, { ...opts, headers });
  } catch (err) {
    throw new ApiError(0, 'network_error', false);
  }
  if (res.status === 401) {
    throw new ApiError(401, 'unauthorized', true);
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    if (data && typeof data === 'object') {
      detail = data.detail
        ? typeof data.detail === 'string'
          ? data.detail
          : JSON.stringify(data.detail)
        : data.error || detail;
    }
    throw new ApiError(res.status, detail, false);
  }
  return data;
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) }),
  del: (path, body) => request(path, { method: 'DELETE', body: JSON.stringify(body) }),
};
