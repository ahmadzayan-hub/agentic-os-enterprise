import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Nav } from "@/components/nav";
import { apiTry, isAuthenticated } from "@/lib/api";
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
  const authenticated = await isAuthenticated();
  if (!authenticated) {
    return (
      <html lang="en">
        <body>{children}</body>
      </html>
    );
  }

  const { data: me } = await apiTry<Principal>("/api/v1/auth/me");
  if (!me) {
    redirect("/login?reason=session");
  }

  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">
          Skip to main content
        </a>
        <div className="app">
          <aside className="sidebar">
            <Link href="/" className="brand" style={{ color: "inherit" }}>
              <span className="brand-mark" aria-hidden="true">
                A
              </span>
              <span>
                <span className="brand-name">Agentic OS</span>
                <br />
                <span className="brand-sub">enterprise 3.1</span>
              </span>
            </Link>
            <Nav />
          </aside>

          <div className="main">
            <header className="topbar">
              <div>
                <div className="mono muted">
                  tenant {me.tenant_id.slice(0, 8)} · clearance {me.clearance}
                </div>
              </div>
              <div className="row">
                <span className="mono muted">{me.email}</span>
                <span className="badge badge-muted">{me.roles.join(", ") || "no roles"}</span>
                {me.mfa_satisfied ? (
                  <span className="badge badge-ok">MFA</span>
                ) : (
                  <span className="badge badge-muted">no MFA</span>
                )}
                <form action="/api/session/logout" method="post">
                  <button className="btn" type="submit">
                    Sign out
                  </button>
                </form>
              </div>
            </header>
            <main className="content" id="main-content">
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
