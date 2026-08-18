"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Primary navigation.
 *
 * Every entry links to a surface that renders real data from the API. There
 * are no placeholder destinations: a capability that is not built does not
 * appear here, it appears in the Capabilities surface marked NOT_IMPLEMENTED.
 */

const GROUPS: { label: string; items: { href: string; label: string }[] }[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Command Center" },
      { href: "/runs", label: "Runs" },
      { href: "/approvals", label: "Approvals" },
      { href: "/operations/incidents", label: "Incidents" },
      { href: "/operations/workflows", label: "Workflows" },
      { href: "/operations/resilience", label: "Resilience" },
    ],
  },
  {
    label: "Build",
    items: [
      { href: "/agents", label: "Agents" },
      { href: "/agents/skills", label: "Skills" },
      { href: "/agents/models", label: "Models" },
      { href: "/agents/prompts", label: "Prompt Registry" },
      { href: "/agents/tools", label: "Tools" },
      { href: "/agents/mcp", label: "MCP Registry" },
    ],
  },
  {
    label: "Know",
    items: [
      { href: "/knowledge", label: "Knowledge" },
      { href: "/knowledge/documents", label: "Documents" },
      { href: "/knowledge/datasets", label: "Datasets" },
      { href: "/knowledge/graph", label: "G-Brain" },
    ],
  },
  {
    label: "Govern",
    items: [
      { href: "/governance/evidence", label: "Evidence" },
      { href: "/governance/policies", label: "Policies" },
      { href: "/governance/risks", label: "Risks" },
      { href: "/governance/audit", label: "Audit" },
      { href: "/governance/privacy", label: "Privacy" },
      { href: "/security", label: "Security" },
    ],
  },
  {
    label: "Measure",
    items: [
      { href: "/operations/analytics", label: "Analytics" },
      { href: "/operations/costs", label: "Cost" },
      { href: "/operations/outcomes", label: "Business Outcomes" },
    ],
  },
  {
    label: "Administer",
    items: [
      { href: "/operations/organization", label: "Organization" },
      { href: "/operations/capabilities", label: "Capabilities" },
    ],
  },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary">
      {GROUPS.map((group) => (
        <div key={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map((item) => {
            const current =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className="nav-link"
                aria-current={current ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
