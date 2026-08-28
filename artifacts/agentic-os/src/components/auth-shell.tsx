import { useEffect, useState } from "react";
import { useLocation, Link } from "wouter";
import { apiFetch } from "@/lib/api";
import { Principal } from "@/lib/types";
import { LanguageSwitch } from "@/components/language-switch";
import { Nav } from "@/components/nav";
import { DEFAULT_LOCALE, directionOf, resolveLocale, translator } from "@/lib/i18n";

export function AuthShell({ children }: { children: React.ReactNode }) {
  const [, navigate] = useLocation();
  const [me, setMe] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);

  // In a real app we might sync locale to localStorage
  const locale = resolveLocale(localStorage.getItem("agentic_locale") || DEFAULT_LOCALE);
  const dir = directionOf(locale);
  const t = translator(locale);

  useEffect(() => {
    // Set dir on document
    document.documentElement.dir = dir;
    document.documentElement.lang = locale;

    apiFetch<Principal>("/api/v1/auth/me")
      .then((data) => {
        setMe(data);
        setLoading(false);
      })
      .catch(() => {
        navigate("/login?reason=session");
      });
  }, [navigate, dir, locale]);

  const handleLogout = async () => {
    try {
      await apiFetch("/api/v1/auth/logout", { method: "POST" });
    } catch {
      // ignore
    }
    navigate("/login");
  };

  if (loading || !me) {
    return null; // or a skeleton loader
  }

  return (
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
            <button className="btn" onClick={handleLogout}>
              {t("chrome.signOut")}
            </button>
          </div>
        </header>
        <main className="content" id="main-content">
          {locale !== DEFAULT_LOCALE ? (
            <p className="notice" role="note" style={{ marginBottom: "1rem" }}>
              {t("notice.untranslated")}
            </p>
          ) : null}
          {children}
        </main>
      </div>
    </div>
  );
}