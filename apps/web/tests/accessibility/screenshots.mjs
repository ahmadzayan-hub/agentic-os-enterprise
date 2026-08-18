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
    await page.fill("#email", process.env.EMAIL ?? "systems.lead@rta.example");
    await page.fill("#password", process.env.PASSWORD ?? "AgenticOS-Demo-2026!");
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
