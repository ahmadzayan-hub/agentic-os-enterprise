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
  
  const response = await fetch(url, {
    ...init,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      ...(init.headers ?? {}),
    },
  });

  const text = await response.text();
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

