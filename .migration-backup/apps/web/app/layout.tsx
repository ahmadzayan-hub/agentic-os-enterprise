import type { Metadata } from "next";
import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";

import { LanguageSwitch } from "@/components/language-switch";
import { Nav } from "@/components/nav";
import { apiTry, isAuthenticated } from "@/lib/api";
import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  directionOf,
  resolveLocale,
  translator,
} from "@/lib/i18n";
import type { Principal } from "@/lib/types";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic OS Enterprise",
  description:
    "Governed enterprise AI control and intelligence platform. Every action is " +
    "identity-aware, policy-controlled, risk-assessed, audited and evidence-backed.",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Direction is decided before anything renders, so the browser lays the page
  // out right-to-left itself rather than the stylesheet trying to mirror it.
  const store = await cookies();
  const locale = resolveLocale(store.get(LOCALE_COOKIE)?.value);
  const dir = directionOf(locale);
  const t = translator(locale);

  const authenticated = await isAuthenticated();
  if (!authenticated) {
    return (
      <html lang={locale} dir={dir}>
        <body>{children}</body>
      </html>
    );
  }

  const { data: me } = await apiTry<Principal>("/api/v1/auth/me");
  if (!me) {
    redirect("/login?reason=session");
  }

  return (
    <html lang={locale} dir={dir}>
      <body>
        <a className="skip-link" href="#main-content">
          {t("app.skipToContent")}
        </a>
        <div className="app">
          <aside className="sidebar">
            <Link href="/" className="brand" style={{ color: "inherit" }}>
              <span className="brand-mark" aria-hidden="true">
                A
              </span>
              <span>
                <span className="brand-name">{t("app.name")}</span>
                <br />
                <span className="brand-sub">{t("app.edition")}</span>
              </span>
            </Link>
            <Nav permissions={me.permissions} locale={locale} />
          </aside>

          <div className="main">
            <header className="topbar">
              <div>
                <div className="mono muted">
                  {t("chrome.tenant")} {me.tenant_id.slice(0, 8)} · {t("chrome.clearance")}{" "}
                  {me.clearance}
                </div>
              </div>
              <div className="row">
                <span className="mono muted">{me.email}</span>
                <span className="badge badge-muted">
                  {me.roles.join(locale === "ar" ? "، " : ", ") || t("chrome.noRoles")}
                </span>
                {me.mfa_satisfied ? (
                  <span className="badge badge-ok">{t("chrome.mfa")}</span>
                ) : (
                  <span className="badge badge-muted">{t("chrome.noMfa")}</span>
                )}
                <LanguageSwitch locale={locale} label={t("chrome.language")} />
                <form action="/api/session/logout" method="post">
                  <button className="btn" type="submit">
                    {t("chrome.signOut")}
                  </button>
                </form>
              </div>
            </header>
            <main className="content" id="main-content">
              {locale !== DEFAULT_LOCALE ? (
                <p className="notice" role="note">
                  {t("notice.untranslated")}
                </p>
              ) : null}
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
