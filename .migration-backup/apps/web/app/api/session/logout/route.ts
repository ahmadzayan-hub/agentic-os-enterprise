import { redirectTo } from "@/lib/redirect";

import { API_BASE, SESSION_COOKIE } from "@/lib/api";

export async function POST(request: Request) {
  const token = request.headers
    .get("cookie")
    ?.split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${SESSION_COOKIE}=`))
    ?.split("=")[1];

  if (token) {
    // Revoke server-side as well as dropping the cookie, so a copied token dies.
    await fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
    }).catch(() => undefined);
  }

  const redirect = redirectTo("/login");
  redirect.cookies.delete(SESSION_COOKIE);
  return redirect;
}
