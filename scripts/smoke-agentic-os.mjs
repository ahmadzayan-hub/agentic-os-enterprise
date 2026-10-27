const base = process.env.API_URL ?? "http://localhost:8080/api/v1";
const password = process.env.DEMO_PASSWORD ?? "Northstar!2026";
const mfaCode = process.env.DEMO_MFA_CODE ?? "123456";

async function request(path, init = {}, cookie = "") {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(cookie ? { cookie } : {}),
      ...(init.headers ?? {}),
    },
  });
  const body = response.status === 204 ? null : await response.json();
  return { response, body, cookie: response.headers.getSetCookie?.()[0]?.split(";")[0] ?? cookie };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const anonymous = await request("/runs");
assert(anonymous.response.status === 401, "anonymous runs request must be rejected");

const readiness = await request("/../readyz");
assert(readiness.response.ok && readiness.body.status === "ready", "database-backed readiness failed");

const untrustedOrigin = await request("/auth/login", {
  method: "POST",
  headers: { origin: "https://attacker.invalid" },
  body: JSON.stringify({ tenant: "northstar-demo", email: "nobody@example.com", password: "invalid" }),
});
assert(untrustedOrigin.response.status === 403, "untrusted mutation origin must be rejected");

const withoutMfa = await request("/auth/login", {
  method: "POST",
  body: JSON.stringify({
    tenant: "northstar-demo",
    email: "alex.morgan@northstar.example",
    password,
  }),
});
assert(withoutMfa.response.ok && !withoutMfa.body.principal.mfa_satisfied, "password login must not assert MFA");
const deniedSecurity = await request("/security/kill-switches", {
  method: "POST",
  body: JSON.stringify({ scope: "GLOBAL", target_key: "", engaged: false, reason: "test", engaged_at: null }),
}, withoutMfa.cookie);
assert(deniedSecurity.response.status === 403, "privileged action must require MFA");

const login = await request("/auth/login", {
  method: "POST",
  body: JSON.stringify({
    tenant: "northstar-demo",
    email: "alex.morgan@northstar.example",
    password,
    mfa_code: mfaCode,
  }),
});
assert(login.response.ok && login.body.principal.mfa_satisfied, "MFA login failed");
const cookie = login.cookie;

const unknownPolicy = await request("/unknown-protected-route", {}, cookie);
assert(unknownPolicy.response.status === 403, "protected routes without an authorization policy must fail closed");

const before = await request("/audit/verify", {}, cookie);
assert(before.response.ok && before.body.intact, "audit chain must begin intact");
const marker = `smoke-${Date.now()}`;
const document = await request("/documents", {
  method: "POST",
  body: JSON.stringify({
    title: marker,
    content: "Persistent governed content created by the automated smoke suite.",
    classification: "INTERNAL",
  }),
}, cookie);
assert(document.response.status === 201, "document ingestion failed");
const after = await request("/audit/verify", {}, cookie);
assert(after.body.intact && after.body.entries_checked === before.body.entries_checked + 1, "mutation and audit append diverged");
const search = await request("/knowledge/search", {
  method: "POST",
  body: JSON.stringify({ query: marker }),
}, cookie);
assert(search.response.ok && search.body.results.some((item) => item.document_id === document.body.document.id), "ingested document was not searchable");

const invalidKillSwitch = await request("/security/kill-switches", {
  method: "POST",
  body: JSON.stringify({ scope: "UNKNOWN", target_key: "", engaged: true, reason: "invalid scope", engaged_at: null }),
}, cookie);
assert(invalidKillSwitch.response.status === 400, "invalid kill-switch scope must be rejected");

await request("/auth/logout", { method: "POST" }, cookie);
const revoked = await request("/auth/me", {}, cookie);
assert(revoked.response.status === 401, "logout did not revoke the session");
console.log("Agentic OS governed-operation smoke suite passed.");