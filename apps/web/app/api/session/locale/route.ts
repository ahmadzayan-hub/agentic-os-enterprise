import { LOCALE_COOKIE, resolveLocale } from "@/lib/i18n";
import { redirectTo } from "@/lib/redirect";

/**
 * Switch the console's language.
 *
 * A POST rather than a GET: it changes stored state, so it must not be
 * reachable by a prefetch or a crawler following a link. The redirect target
 * is rebuilt from the request's own origin rather than taken from the form, so
 * this cannot be used as an open redirect.
 */
export async function POST(request: Request) {
  const form = await request.formData();
  const locale = resolveLocale(String(form.get("locale") ?? ""));

  const raw = String(form.get("next") ?? "/");
  // Same-origin, path-only. A value like "//evil.example" or "https://…" is
  // discarded rather than sanitised, because guessing intent is how open
  // redirects get shipped.
  const next = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";

  const response = redirectTo(next);
  response.cookies.set(LOCALE_COOKIE, locale, {
    path: "/",
    sameSite: "lax",
    httpOnly: false, // a display preference, not a credential
    maxAge: 60 * 60 * 24 * 365,
  });
  return response;
}
