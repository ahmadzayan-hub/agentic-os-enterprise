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

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (pathname === "/login" && hasSession) {
    // Already signed in: sending the shell plus a second sign-in form would
    // nest one <main> landmark inside another.
    return NextResponse.redirect(new URL("/", request.url));
  }
  if (PUBLIC_PATHS.some((path) => pathname.startsWith(path))) {
    return NextResponse.next();
  }
  if (hasSession) {
    return NextResponse.next();
  }

  const login = new URL("/login", request.url);
  if (pathname !== "/") {
    login.searchParams.set("next", pathname);
  }
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
