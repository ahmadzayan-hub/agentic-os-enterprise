import { useState } from "react";
import { useLocation } from "wouter";
import { apiFetch, ApiError } from "@/lib/api";

export default function LoginPage() {
  const [, navigate] = useLocation();
  const searchParams = new URLSearchParams(window.location.search);
  const mfaRequired = searchParams.get("mfa") === "1";
  const reason = searchParams.get("reason");
  const defaultEmail = searchParams.get("email") ?? "";
  
  const [error, setError] = useState<string | null>(searchParams.get("error"));
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError(null);
    
    const formData = new FormData(e.currentTarget);
    const email = formData.get("email") as string;
    const password = formData.get("password") as string;
    const mfaCode = formData.get("mfa_code") as string;

    try {
      await apiFetch<{ authenticated: boolean }>('/api/v1/auth/login', {
        method: "POST",
        body: JSON.stringify({
          email,
          password,
          ...(mfaCode ? { mfa_code: mfaCode.trim() } : {})
        })
      });
      
      navigate("/");
    } catch (err) {
      if (err instanceof ApiError) {
        const p = err.payload as any;
        const mfaReq = p?.detail?.details?.mfa_required;
        if (mfaReq) {
          navigate(`/login?mfa=1&email=${encodeURIComponent(email)}`);
        } else {
          setError(err.message || "Sign-in failed");
        }
      } else {
        setError("Network error");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

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

          {reason === "session" ? (
            <div style={{ marginBottom: 14 }}>
              <div className="notice notice-warn">Your session ended. Sign in again.</div>
            </div>
          ) : null}

          {error ? (
            <div style={{ marginBottom: 14 }}>
              <div className="notice notice-danger" role="alert">
                {error}
              </div>
            </div>
          ) : null}

          <form onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                defaultValue={defaultEmail}
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
            <button className="btn btn-primary" type="submit" disabled={isSubmitting} style={{ width: "100%" }}>
              {isSubmitting ? "Signing in..." : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}