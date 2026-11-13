export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public payload: unknown,
    message: string,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = path.startsWith('/api/v1') ? path : `${API_BASE}${path}`;
  
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      credentials: "same-origin",
      headers: {
        "content-type": "application/json",
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    // The request never reached a server: offline, DNS failure, or no API
    // behind this origin. Status 0 marks it as unreachable for callers.
    throw new ApiError(
      0,
      null,
      error instanceof Error ? error.message : "network error",
    );
  }

  const text = await response.text();
  const contentType = response.headers.get("content-type") ?? "";

  // A static host (for example a single-page fallback rewrite) answers every
  // path with index.html and status 200. Treating that HTML as API data would
  // render an empty principal and crash the shell, so it is refused here.
  if (response.ok && text && !contentType.includes("json")) {
    throw new ApiError(0, null, "API did not return JSON");
  }

  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail =
      (payload as { detail?: { message?: string } })?.detail?.message ??
      (payload as { message?: string })?.message ??
      response.statusText;
    throw new ApiError(response.status, payload, detail);
  }
  return payload as T;
}

/**
 * True when the failure means the console cannot reach a working API at all,
 * as opposed to the API answering with a real error. Gateway errors from a
 * proxy and a 404 on the API root both count: there is no API to talk to.
 */
export function isApiUnavailable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (
    error.status === 0 ||
    error.status === 404 ||
    error.status === 502 ||
    error.status === 503 ||
    error.status === 504
  );
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

export async function apiTry<T>(
  path: string,
): Promise<{ data: T | null; error: string | null; status: number }> {
  try {
    return { data: await apiFetch<T>(path), error: null, status: 200 };
  } catch (error) {
    if (error instanceof ApiError) {
      return { data: null, error: error.message, status: error.status };
    }
    return {
      data: null,
      error: error instanceof Error ? error.message : "unknown error",
      status: 0,
    };
  }
}

