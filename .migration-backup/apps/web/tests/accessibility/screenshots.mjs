/** Capture screenshots of the running application for review evidence. */
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const OUT = process.env.OUT ?? "../../docs/screenshots";
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM_PATH ?? "";

const SHOTS = [
  { path: "/login", name: "01-sign-in", auth: false, scheme: "light" },
  { path: "/", name: "02-command-center", scheme: "light" },
  { path: "/", name: "03-command-center-dark", scheme: "dark" },
  { path: "/runs", name: "04-runs", scheme: "light" },
  { path: "__RUN__", name: "05-run-detail", scheme: "light", full: true },
  { path: "/approvals", name: "06-approvals", scheme: "light" },
  { path: "/agents", name: "07-agents", scheme: "light" },
  { path: "/agents/operations", name: "08-agent-contract", scheme: "light", full: true },
  { path: "/governance/evidence", name: "09-evidence-maturity", scheme: "light", full: true },
  { path: "/governance/audit", name: "10-audit-ledger", scheme: "light" },
  { path: "/agents/tools", name: "11-tool-registry", scheme: "dark" },
  { path: "/operations/capabilities", name: "12-capabilities", scheme: "dark", full: true },
  { path: "/knowledge", name: "13-knowledge-search", scheme: "light" },
  // The privacy register needs privacy:read, which the operator role does not
  // carry; captured as the governance officer instead of showing a denial.
  {
    path: "/governance/privacy",
    name: "14-privacy-register",
    scheme: "light",
    full: true,
    email: "governance@rta.example",
    // Privileged roles must present a second factor. The code is minted by
    // scripts/dev_totp.py and passed in; no secret is stored here.
    totpEnv: "GOVERNANCE_TOTP",
  },
  { path: "/operations/resilience", name: "15-resilience", scheme: "light", full: true },
  { path: "/operations/costs", name: "16-cost", scheme: "dark" },
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch(EXECUTABLE ? { executablePath: EXECUTABLE } : {});
let runPath = "/runs";

for (const shot of SHOTS) {
  const context = await browser.newContext({
    colorScheme: shot.scheme,
    viewport: { width: 1560, height: 1000 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  if (shot.auth !== false) {
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await page.fill("#email", shot.email ?? process.env.EMAIL ?? "systems.lead@rta.example");
    await page.fill("#password", process.env.PASSWORD ?? "AgenticOS-Demo-2026!");
    if (shot.totpEnv) {
      const code = process.env[shot.totpEnv];
      if (!code) {
        throw new Error(
          `${shot.name} signs in as a privileged role and needs ${shot.totpEnv}; ` +
            "mint one with: python scripts/dev_totp.py <email>",
        );
      }
      await page.fill("#mfa_code", code);
    }
    await page.click('button[type="submit"]');
    await page.waitForLoadState("networkidle");

    if (runPath === "/runs") {
      await page.goto(`${BASE}/runs`, { waitUntil: "networkidle" });
      const href = await page.locator('a[href^="/runs/"]').first().getAttribute("href");
      if (href) runPath = href;
    }
  }

  const target = shot.path === "__RUN__" ? runPath : shot.path;
  await page.goto(`${BASE}${target}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${shot.name}.png`, fullPage: Boolean(shot.full) });
  console.log(`captured ${shot.name} -> ${target} (${shot.scheme})`);
  await context.close();
}

await browser.close();
