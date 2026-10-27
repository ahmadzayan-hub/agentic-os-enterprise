import { cookies } from "next/headers";

/**
 * Server-side API client.
 *
 * The access token lives in an httpOnly cookie and is attached here, so it is
 * never exposed to client JavaScript and cannot be read by an injected script.
 * Every page renders on the server against the caller's own permissions, which
 * means the UI shows exactly what the API would return — never a superset.
 */

export const API_BASE =
  process.env.AGENTIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export const SESSION_COOKIE = "agentic_session";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly payload: unknown,
    message: string,
  ) {
    super(message);
  }
}

async function token(): Promise<string | undefined> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const accessToken = await token();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "content-type": "application/json",
      ...(accessToken ? { authorization: `Bearer ${accessToken}` } : {}),
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

/** Fetch that renders a surface as unavailable rather than crashing the page. */
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

export async function isAuthenticated(): Promise<boolean> {
  return Boolean(await token());
}
