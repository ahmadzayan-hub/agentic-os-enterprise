/**
 * WCAG 2.2 AA accessibility audit.
 *
 * Drives the real application in a real browser and runs axe-core against every
 * primary surface, in both colour schemes. Violations are written to a JSON
 * report that the Evidence Engine consumes for control UX-003.
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
];

// WCAG 2.2 AA plus the best-practice rules that catch real navigation problems.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

async function main() {
  const browser = await chromium.launch(
    EXECUTABLE ? { executablePath: EXECUTABLE } : {},
  );
  const results = [];
  let serious = 0;
  let total = 0;

  for (const scheme of ["light", "dark"]) {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width: 1440, height: 960 },
    });
    const page = await context.newPage();

    // Authenticate once per context using the real login form.
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await page.fill("#email", EMAIL);
    await page.fill("#password", PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForLoadState("networkidle");

    for (const surface of SURFACES) {
      await page.goto(`${BASE}${surface.path}`, { waitUntil: "networkidle" });
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
        violations,
        passes: scan.passes.length,
        incomplete: scan.incomplete.length,
      });
    }
    await context.close();
  }

  await browser.close();

  const report = {
    tool: "axe-core via playwright",
    tags: TAGS,
    generated_at: new Date().toISOString(),
    surfaces_scanned: SURFACES.length,
    color_schemes: ["light", "dark"],
    total_violations: total,
    serious_or_critical: serious,
    passed: serious === 0,
    results,
  };

  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(report, null, 2));

  console.log(
    `axe: ${SURFACES.length} surfaces x 2 colour schemes — ` +
      `${total} violations, ${serious} serious or critical`,
  );
  for (const entry of results) {
    for (const violation of entry.violations) {
      console.log(
        `  ${entry.colorScheme} ${entry.path}: [${violation.impact}] ${violation.id} — ` +
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
