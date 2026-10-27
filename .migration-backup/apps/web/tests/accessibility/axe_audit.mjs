/**
 * WCAG 2.2 AA accessibility audit.
 *
 * Drives the real application in a real browser and runs axe-core against every
 * primary surface, in both colour schemes and both writing directions.
 * Violations are written to a JSON report that the Evidence Engine consumes for
 * control UX-003.
 *
 * The right-to-left pass is not a formality. Mirroring a layout is where
 * overlapping controls, clipped focus rings and reversed reading order show up,
 * and none of those are visible to someone testing in English only.
 *
 *   node tests/accessibility/axe_audit.mjs --base http://127.0.0.1:3000 \
 *        --email operator@example --password ... --out artifacts/accessibility.json
 *
 * Exit code is non-zero when any serious or critical violation is found, so the
 * CI job fails on a real regression rather than merely recording it.
 */

import { writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const BASE = arg("base", "http://127.0.0.1:3000");
const EMAIL = arg("email", "systems.lead@rta.example");
const PASSWORD = arg("password", "AgenticOS-Demo-2026!");
const OUT = arg("out", "artifacts/accessibility.json");

/**
 * Chromium location.
 *
 * Playwright resolves a build-pinned path by default, which breaks whenever the
 * package version and the pre-provisioned browser drift apart. Accepting an
 * explicit path (or PLAYWRIGHT_CHROMIUM_PATH) lets CI and pre-baked images
 * supply whatever Chromium they already have.
 */
const EXECUTABLE = arg("chromium", process.env.PLAYWRIGHT_CHROMIUM_PATH ?? "");

const SURFACES = [
  { path: "/login", label: "Sign in", authenticated: false },
  { path: "/", label: "Command Center" },
  { path: "/decisions", label: "Decision Queue" },
  { path: "/notifications", label: "Inbox" },
  { path: "/runs", label: "Runs" },
  { path: "/approvals", label: "Approvals" },
  { path: "/agents", label: "Agents" },
  { path: "/agents/skills", label: "Skills" },
  { path: "/agents/models", label: "Models" },
  { path: "/agents/tools", label: "Tools" },
  { path: "/knowledge", label: "Knowledge" },
  { path: "/knowledge/documents", label: "Documents" },
  { path: "/knowledge/graph", label: "G-Brain" },
  { path: "/governance/evidence", label: "Evidence" },
  { path: "/governance/policies", label: "Policies" },
  { path: "/governance/audit", label: "Audit" },
  { path: "/governance/privacy", label: "Privacy" },
  { path: "/security", label: "Security" },
  { path: "/operations/analytics", label: "Analytics" },
  { path: "/operations/costs", label: "Cost" },
  { path: "/operations/outcomes", label: "Outcomes" },
  { path: "/operations/capabilities", label: "Capabilities" },
  { path: "/operations/resilience", label: "Resilience" },
  { path: "/operations/workflows", label: "Workflows" },
  { path: "/operations/incidents", label: "Incidents" },
  { path: "/operations/alerts", label: "Alerts" },
];

// WCAG 2.2 AA plus the best-practice rules that catch real navigation problems.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

//: Locale drives `<html lang>` and `<html dir>`, so this is the direction axis.
const LOCALES = [
  { locale: "en", dir: "ltr" },
  { locale: "ar", dir: "rtl" },
];

async function main() {
  const browser = await chromium.launch(
    EXECUTABLE ? { executablePath: EXECUTABLE } : {},
  );
  const results = [];
  let serious = 0;
  let total = 0;

  for (const scheme of ["light", "dark"]) {
   for (const { locale, dir } of LOCALES) {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width: 1440, height: 960 },
    });
    // The console reads its language from this cookie on every server render,
    // so setting it before the first navigation means even the sign-in page is
    // audited in the right direction.
    await context.addCookies([{ name: "agentic_locale", value: locale, url: BASE }]);
    const page = await context.newPage();

    // Authenticate once per context using the real login form.
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await page.fill("#email", EMAIL);
    await page.fill("#password", PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForLoadState("networkidle");

    // A failed sign-in is the quiet way this audit lies. Every surface would
    // redirect to /login, axe would scan the login page twenty-five times, and
    // the report would say "0 serious violations" about pages it never loaded.
    // That happened: the API's rate limiter refused the second context's login
    // after the first pass had spent the budget, and the run looked clean.
    // Checked as "landed on the console", not as "is no longer on /login".
    // The weaker test let a failure through: when the API was unreachable the
    // sign-in route threw, the browser stopped on a 500 at
    // /api/session/login — which does not start with "/login" — and the audit
    // carried on to scan twenty-five redirects to the sign-in page and report
    // them clean. A guard that only rules out the one failure you thought of
    // is how this audit lies.
    const landedAfterSignIn = new URL(page.url()).pathname;
    if (landedAfterSignIn !== "/") {
      throw new Error(
        `sign-in failed for ${scheme}/${locale}: landed on ${page.url()} rather ` +
          `than the console. Check the API is reachable at the address the ` +
          `console is configured with, and raise AGENTIC_RATE_LIMIT_PER_MINUTE ` +
          `on it — the audit signs in four times.`,
      );
    }

    for (const surface of SURFACES) {
      await page.goto(`${BASE}${surface.path}`, { waitUntil: "networkidle" });

      // The same failure can appear mid-run if the session is lost, so each
      // authenticated surface confirms it is actually the surface it claims.
      const landed = new URL(page.url()).pathname;
      if (surface.authenticated !== false && landed !== surface.path) {
        throw new Error(
          `${surface.path} redirected to ${landed} during ${scheme}/${locale}; ` +
            `the session was lost and the remaining results would be meaningless`,
        );
      }

      // A direction mismatch would make every RTL result meaningless while
      // still reporting zero violations, so it fails loudly instead.
      const rendered = await page.getAttribute("html", "dir");
      if (rendered !== dir) {
        throw new Error(
          `${surface.path} rendered dir="${rendered}" for locale ${locale}, expected "${dir}"`,
        );
      }

      const scan = await new AxeBuilder({ page }).withTags(TAGS).analyze();

      const violations = scan.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        help: violation.help,
        nodes: violation.nodes.length,
        target: violation.nodes[0]?.target?.join(" ") ?? "",
      }));
      total += violations.length;
      serious += violations.filter((v) => ["serious", "critical"].includes(v.impact)).length;

      results.push({
        surface: surface.label,
        path: surface.path,
        colorScheme: scheme,
        locale,
        direction: dir,
        violations,
        passes: scan.passes.length,
        incomplete: scan.incomplete.length,
      });
    }
    await context.close();
   }
  }

  await browser.close();

  const report = {
    tool: "axe-core via playwright",
    tags: TAGS,
    generated_at: new Date().toISOString(),
    surfaces_scanned: SURFACES.length,
    color_schemes: ["light", "dark"],
    locales: LOCALES.map((entry) => entry.locale),
    directions: LOCALES.map((entry) => entry.dir),
    scans: results.length,
    total_violations: total,
    serious_or_critical: serious,
    passed: serious === 0,
    results,
  };

  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(report, null, 2));

  console.log(
    `axe: ${SURFACES.length} surfaces x 2 colour schemes x ${LOCALES.length} directions ` +
      `= ${results.length} scans — ${total} violations, ${serious} serious or critical`,
  );
  for (const entry of results) {
    for (const violation of entry.violations) {
      console.log(
        `  ${entry.colorScheme}/${entry.direction} ${entry.path}: ` +
          `[${violation.impact}] ${violation.id} — ` +
          `${violation.help} (${violation.nodes} node(s), first: ${violation.target})`,
      );
    }
  }
  process.exit(serious === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error(error);
  process.exit(2);
});
