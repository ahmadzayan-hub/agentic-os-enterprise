import { useEffect, useState } from "react";
import { useLocation, Link } from "wouter";
import { apiFetch, ApiError } from "@/lib/api";
import { Principal } from "@/lib/types";
import { LanguageSwitch } from "@/components/language-switch";
import { Nav } from "@/components/nav";
import { DEFAULT_LOCALE, directionOf, resolveLocale, translator } from "@/lib/i18n";

export function AuthShell({ children }: { children: React.ReactNode }) {
  const [, navigate] = useLocation();
  const [me, setMe] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [navigationOpen, setNavigationOpen] = useState(false);

  // In a real app we might sync locale to localStorage
  const locale = resolveLocale(localStorage.getItem("agentic_locale") || DEFAULT_LOCALE);
  const dir = directionOf(locale);
  const t = translator(locale);

  useEffect(() => {
    // Set dir on document
    document.documentElement.dir = dir;
    document.documentElement.lang = locale;

    setLoading(true);
    setLoadError(false);
    apiFetch<Principal>("/api/v1/auth/me")
      .then((data) => {
        setMe(data);
      })
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) {
          navigate("/login?reason=session");
          return;
        }
        setLoadError(true);
      })
      .finally(() => setLoading(false));
  }, [navigate, dir, locale, revision]);

  const handleLogout = async () => {
    try {
      await apiFetch("/api/v1/auth/logout", { method: "POST" });
    } catch {
      // ignore
    }
    navigate("/login");
  };

  if (loading) {
    return <div className="session-state" role="status">{t("chrome.loadingSession")}</div>;
  }

  if (loadError || !me) {
    return (
      <div className="session-state" role="alert">
        <p>{t("chrome.sessionError")}</p>
        <button className="btn btn-primary" onClick={() => setRevision((value) => value + 1)}>
          {t("chrome.retry")}
        </button>
      </div>
    );
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">{t("app.skipToContent")}</a>
      {navigationOpen ? (
        <button
          className="mobile-nav-backdrop"
          aria-label={t("chrome.closeNavigation")}
          onClick={() => setNavigationOpen(false)}
        />
      ) : null}
      <aside
        className={`sidebar ${navigationOpen ? "sidebar-open" : ""}`}
        id="primary-navigation"
        style={navigationOpen ? { transform: "translateX(0)" } : undefined}
      >
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
        <button
          className="btn mobile-nav-close"
          type="button"
          onClick={() => setNavigationOpen(false)}
        >
          {t("chrome.closeNavigation")}
        </button>
        <Nav permissions={me.permissions} locale={locale} onNavigate={() => setNavigationOpen(false)} />
      </aside>

      <div className="main">
        <header className="topbar">
          <button
            className="btn mobile-nav-trigger"
            type="button"
            aria-expanded={navigationOpen}
            aria-controls="primary-navigation"
            onClick={() => setNavigationOpen(true)}
          >
            {t("chrome.openNavigation")}
          </button>
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