import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/api";

/**
 * Authentication gate.
 *
 * Anything outside the login flow requires a session cookie. This is a
 * usability boundary, not the security boundary: the API independently
 * authenticates and authorises every request, so a forged cookie gets a caller
 * past this redirect and no further.
 */
const PUBLIC_PATHS = ["/login", "/api/session/login"];

/**
 * The origin the browser actually asked for.
 *
 * Not `request.url`. In the standalone server that carries the host Next
 * resolved rather than the one in the request, so a console reached on
 * `127.0.0.1:3031` was redirected to `http://localhost:3031/login`. Those are
 * different origins to a browser: the session and locale cookies just set for
 * `127.0.0.1` were not sent with the follow-up request, so the user landed
 * back on the sign-in page. The accessibility audit found it — its
 * right-to-left pass rendered `dir="ltr"` because the locale cookie had been
 * dropped in exactly that way — which is the argument for having the audit
 * fail loudly on a direction mismatch instead of scanning on regardless.
 *
 * `lib/redirect.ts` made this same argument for the route handlers and fixed
 * it by emitting a path-only Location. That is not available here: Next's
 * middleware runtime validates the header and rejects a relative URL outright
 * (ERR_INVALID_URL), so the origin has to be reconstructed. It is taken from
 * the forwarding headers, so behind a proxy the browser is returned to the
 * host it actually used rather than to whatever the upstream connection is
 * named — which is the deployment that matters.
 *
 * What this does *not* fix, measured rather than assumed: when the
 * reconstructed origin has the same port Next is listening on, Next rewrites
 * the Location host to its own resolved hostname anyway. Serving on
 * `127.0.0.1:3037` and asking for `127.0.0.1:3037` still yields
 * `http://localhost:3037/...`; asking for `127.0.0.1:9999` is left alone. So a
 * console reached by one loopback alias and configured under another still
 * loses its cookies on the first redirect, and no middleware code can prevent
 * it. Reach the console on the hostname it is served under.
 *
 * The Host header is client-supplied. It is used *only* to echo the caller
 * back to the host they themselves named, on a fixed path chosen here — never
 * to build a link, a link target, or anything stored. A caller who forges it
 * redirects themselves and nobody else.
 */
function origin(request: NextRequest): string {
  const forwardedHost = request.headers.get("x-forwarded-host");
  const host = forwardedHost ?? request.headers.get("host");
  if (!host) return request.nextUrl.origin;
  const proto =
    request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() ??
    request.nextUrl.protocol.replace(":", "");
  return `${proto}://${host}`;
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (pathname === "/login" && hasSession) {
    // Already signed in: sending the shell plus a second sign-in form would
    // nest one <main> landmark inside another.
    return NextResponse.redirect(`${origin(request)}/`);
  }
  if (PUBLIC_PATHS.some((path) => pathname.startsWith(path))) {
    return NextResponse.next();
  }
  if (hasSession) {
    return NextResponse.next();
  }

  // `pathname` comes from the request line, so it is encoded as a query value
  // rather than interpolated into the path. The sign-in page decides for
  // itself whether to follow it.
  const next = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
  return NextResponse.redirect(`${origin(request)}/login${next}`);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
