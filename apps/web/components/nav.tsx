"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Primary navigation.
 *
 * Every entry links to a surface that renders real data from the API. There
 * are no placeholder destinations: a capability that is not built does not
 * appear here, it appears in the Capabilities surface marked NOT_IMPLEMENTED.
 *
 * Entries are also filtered by the signed-in principal's permissions, so a
 * link that would only ever return "not permitted" is not shown. This is
 * presentation, not enforcement: the API authorises every request on its own
 * and a hidden link is still refused if it is typed into the address bar.
 */

type NavItem = { href: string; label: string; permission?: string };

const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Command Center", permission: "analytics:read" },
      { href: "/runs", label: "Runs", permission: "runs:read" },
      { href: "/approvals", label: "Approvals", permission: "approvals:read" },
      { href: "/operations/incidents", label: "Incidents", permission: "incidents:read" },
      { href: "/operations/workflows", label: "Workflows", permission: "workflows:read" },
      { href: "/operations/resilience", label: "Resilience", permission: "incidents:read" },
    ],
  },
  {
    label: "Build",
    items: [
      { href: "/agents", label: "Agents", permission: "agents:read" },
      { href: "/agents/skills", label: "Skills", permission: "skills:read" },
      { href: "/agents/models", label: "Models", permission: "models:read" },
      { href: "/agents/prompts", label: "Prompt Registry", permission: "prompts:read" },
      { href: "/agents/tools", label: "Tools", permission: "tools:read" },
      { href: "/agents/mcp", label: "MCP Registry", permission: "mcp:read" },
    ],
  },
  {
    label: "Know",
    items: [
      { href: "/knowledge", label: "Knowledge", permission: "knowledge:read" },
      { href: "/knowledge/documents", label: "Documents", permission: "knowledge:read" },
      { href: "/knowledge/datasets", label: "Datasets", permission: "knowledge:read" },
      { href: "/knowledge/graph", label: "G-Brain", permission: "graph:read" },
    ],
  },
  {
    label: "Govern",
    items: [
      { href: "/governance/evidence", label: "Evidence", permission: "evidence:read" },
      { href: "/governance/policies", label: "Policies", permission: "policies:read" },
      { href: "/governance/risks", label: "Risks", permission: "risks:read" },
      { href: "/governance/audit", label: "Audit", permission: "audit:read" },
      { href: "/governance/privacy", label: "Privacy", permission: "privacy:read" },
      { href: "/security", label: "Security", permission: "security:read" },
    ],
  },
  {
    label: "Measure",
    items: [
      { href: "/operations/analytics", label: "Analytics", permission: "analytics:read" },
      { href: "/operations/costs", label: "Cost", permission: "costs:read" },
      { href: "/operations/outcomes", label: "Business Outcomes", permission: "outcomes:read" },
    ],
  },
  {
    label: "Administer",
    items: [
      { href: "/operations/organization", label: "Organization", permission: "org:read" },
      { href: "/operations/capabilities", label: "Capabilities" },
    ],
  },
];

export function Nav({ permissions = [] }: { permissions?: string[] }) {
  const pathname = usePathname();
  const granted = new Set(permissions);
  const allowed = (item: NavItem) =>
    !item.permission || granted.has("*") || granted.has(item.permission);

  const groups = GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(allowed),
  })).filter((group) => group.items.length > 0);

  return (
    <nav aria-label="Primary">
      {groups.map((group) => (
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
