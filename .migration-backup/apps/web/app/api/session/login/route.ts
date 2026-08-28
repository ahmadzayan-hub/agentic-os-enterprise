import { NextResponse } from "next/server";

import { API_BASE, SESSION_COOKIE } from "@/lib/api";

/**
 * Exchange credentials for a session cookie.
 *
 * The access token is stored httpOnly so client JavaScript can never read it,
 * which means an XSS bug cannot exfiltrate a working credential.
 */
export async function POST(request: Request) {
  const form = await request.formData();
  const email = String(form.get("email") ?? "");
  const password = String(form.get("password") ?? "");
  const mfaCode = String(form.get("mfa_code") ?? "").trim();

  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      ...(mfaCode ? { mfa_code: mfaCode } : {}),
    }),
    cache: "no-store",
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: { message?: string; details?: { mfa_required?: boolean } } }
      | null;
    const mfaRequired = payload?.detail?.details?.mfa_required === true;
    const message = payload?.detail?.message ?? "Sign-in failed";
    const url = new URL("/login", request.url);
    url.searchParams.set("error", message);
    if (mfaRequired) url.searchParams.set("mfa", "1");
    url.searchParams.set("email", email);
    return NextResponse.redirect(url, { status: 303 });
  }

  const body = (await response.json()) as { access_token: string; expires_in: number };
  const redirect = NextResponse.redirect(new URL("/", request.url), { status: 303 });
  redirect.cookies.set({
    name: SESSION_COOKIE,
    value: body.access_token,
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: body.expires_in,
  });
  return redirect;
}
