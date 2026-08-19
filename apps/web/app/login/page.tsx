import type { Metadata } from "next";

export const metadata: Metadata = { title: "Sign in · Agentic OS" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; mfa?: string; email?: string; reason?: string }>;
}) {
  const params = await searchParams;
  const mfaRequired = params.mfa === "1";

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
      }}
    >
      <div style={{ width: "100%", maxWidth: 420 }}>
        <div className="brand" style={{ paddingLeft: 0, marginBottom: 6 }}>
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>
            <span className="brand-name">Agentic OS</span>
            <br />
            <span className="brand-sub">enterprise 3.1</span>
          </span>
        </div>

        <div className="card">
          <h1>Sign in</h1>
          <p className="page-lede" style={{ marginBottom: 18 }}>
            Governed enterprise AI control plane. Access is scoped to your tenant,
            roles and data clearance.
          </p>

          {params.reason === "session" ? (
            <div style={{ marginBottom: 14 }}>
              <div className="notice notice-warn">Your session ended. Sign in again.</div>
            </div>
          ) : null}

          {params.error ? (
            <div style={{ marginBottom: 14 }}>
              <div className="notice notice-danger" role="alert">
                {params.error}
              </div>
            </div>
          ) : null}

          <form action="/api/session/login" method="post">
            <div className="field">
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                defaultValue={params.email ?? ""}
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="mfa_code">
                Authenticator code {mfaRequired ? "(required)" : "(if enrolled)"}
              </label>
              <input
                id="mfa_code"
                name="mfa_code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={8}
                aria-describedby="mfa-hint"
                required={mfaRequired}
              />
              <p className="field-hint" id="mfa-hint">
                Privileged roles — approver, auditor, security, governance and platform
                administration — always require a second factor.
              </p>
            </div>
            <button className="btn btn-primary" type="submit" style={{ width: "100%" }}>
              Sign in
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
