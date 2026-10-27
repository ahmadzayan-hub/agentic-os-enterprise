import { NextResponse } from "next/server";

/**
 * A redirect to a path on this same origin.
 *
 * Deliberately emits a *relative* Location header rather than an absolute URL.
 * `new URL(path, request.url)` looks equivalent and is not: in the standalone
 * server `request.url` carries the bind address, so a console reachable on
 * 127.0.0.1 redirected the browser to `http://0.0.0.0:3000/`. That is a
 * different origin, so the session cookie just set on the response was not
 * sent with the follow-up request and the user landed back on the sign-in
 * page. Behind a proxy the same construction takes its host from whatever
 * upstream happened to pass along, which is worse.
 *
 * A path-only Location is valid (RFC 9110 §10.2.2), is resolved by the client
 * against the request it actually made, and cannot name another origin at all.
 *
 * Callers must pass a path they constructed. Anything derived from user input
 * has to be checked first — this helper deliberately does not sanitise, since
 * guessing intent is how open redirects get shipped.
 */
export function redirectTo(path: string, params?: Record<string, string>): NextResponse {
  const query = params
    ? Object.entries(params)
        .filter(([, value]) => value !== "")
        .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
        .join("&")
    : "";
  const location = query ? `${path}?${query}` : path;
  return new NextResponse(null, { status: 303, headers: { location } });
}
