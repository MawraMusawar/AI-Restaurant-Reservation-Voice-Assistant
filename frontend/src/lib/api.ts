/**
 * Same-origin calls to the FastAPI backend (Next.js proxies /api/* to it).
 * The admin session is an HttpOnly cookie, so nothing secret lives in JS.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The parsed `detail` (string, or an object such as a booking problem). */
    readonly detail: unknown,
  ) {
    super(message);
  }
}

function messageFrom(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    const inner = (detail as { detail: unknown }).detail;
    if (typeof inner === "string") return inner;
  }
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
      .join("; ");
  }
  if (status === 0) return "Can't reach the backend. Is it running?";
  return `Request failed (${status})`;
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  let response: Response;
  try {
    response = await fetch(path, {
      ...rest,
      credentials: "same-origin",
      headers: json !== undefined ? { "Content-Type": "application/json", ...rest.headers } : rest.headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw new ApiError(0, messageFrom(null, 0), null);
  }
  if (response.ok) return (await response.json()) as T;
  let detail: unknown = null;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail ?? null;
  } catch {
    // Not JSON (e.g. the proxy could not reach the backend).
  }
  const message =
    response.status >= 500 && detail === null
      ? "Can't reach the backend. Is it running?"
      : messageFrom(detail, response.status);
  throw new ApiError(response.status, message, detail);
}
