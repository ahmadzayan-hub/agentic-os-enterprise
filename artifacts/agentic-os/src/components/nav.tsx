
import { Link } from 'wouter';
import { useLocation } from 'wouter';

import { type Locale, type MessageKey, translator } from "@/lib/i18n";

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
 *
 * Labels are message keys, not literals, so the navigation reads in the
 * signed-in user's language and a missing translation fails to compile.
 */

type NavItem = { href: string; label: MessageKey; permission?: string };

const GROUPS: { label: MessageKey; items: NavItem[] }[] = [
  {
    label: "nav.group.operate",
    items: [
      { href: "/", label: "nav.commandCenter", permission: "analytics:read" },
      { href: "/runs", label: "nav.runs", permission: "runs:read" },
      { href: "/approvals", label: "nav.approvals", permission: "approvals:read" },
      { href: "/operations/incidents", label: "nav.incidents", permission: "platform:read" },
      { href: "/operations/workflows", label: "nav.workflows", permission: "platform:read" },
      { href: "/operations/resilience", label: "nav.resilience", permission: "platform:read" },
    ],
  },
  {
    label: "nav.group.build",
    items: [
      { href: "/agents", label: "nav.agents", permission: "agents:read" },
      { href: "/agents/skills", label: "nav.skills", permission: "agents:read" },
      { href: "/agents/models", label: "nav.models", permission: "agents:read" },
      { href: "/agents/prompts", label: "nav.prompts", permission: "agents:read" },
      { href: "/agents/tools", label: "nav.tools", permission: "agents:read" },
      { href: "/agents/mcp", label: "nav.mcp", permission: "agents:read" },
    ],
  },
  {
    label: "nav.group.know",
    items: [
      { href: "/knowledge", label: "nav.knowledge", permission: "knowledge:read" },
      { href: "/knowledge/documents", label: "nav.documents", permission: "documents:read" },
      { href: "/knowledge/datasets", label: "nav.datasets", permission: "knowledge:read" },
      { href: "/knowledge/graph", label: "nav.graph", permission: "knowledge:read" },
    ],
  },
  {
    label: "nav.group.govern",
    items: [
      { href: "/governance/evidence", label: "nav.evidence", permission: "governance:read" },
      { href: "/governance/policies", label: "nav.policies", permission: "governance:read" },
      { href: "/governance/risks", label: "nav.risks", permission: "governance:read" },
      { href: "/governance/audit", label: "nav.audit", permission: "governance:read" },
      { href: "/governance/privacy", label: "nav.privacy", permission: "governance:read" },
      { href: "/security", label: "nav.security", permission: "security:read" },
    ],
  },
  {
    label: "nav.group.measure",
    items: [
      { href: "/operations/analytics", label: "nav.analytics", permission: "analytics:read" },
      { href: "/operations/costs", label: "nav.costs", permission: "platform:read" },
      { href: "/operations/outcomes", label: "nav.outcomes", permission: "platform:read" },
    ],
  },
  {
    label: "nav.group.administer",
    items: [
      { href: "/operations/organization", label: "nav.organization", permission: "organization:read" },
      { href: "/operations/capabilities", label: "nav.capabilities", permission: "platform:read" },
    ],
  },
];

export function Nav({
  permissions = [],
  locale,
  onNavigate,
}: {
  permissions?: string[];
  locale: Locale;
  onNavigate?: () => void;
}) {
  const [pathname] = useLocation();
  const t = translator(locale);
  const granted = new Set(permissions);
  const allowed = (item: NavItem) =>
    !item.permission || granted.has("*") || granted.has(item.permission);

  const groups = GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(allowed),
  })).filter((group) => group.items.length > 0);

  return (
    <nav aria-label={t("nav.primary")}>
      {groups.map((group) => (
        <div key={group.label}>
          <div className="nav-group-label">{t(group.label)}</div>
          {group.items.map((item) => {
            const current =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className="nav-link"
                aria-current={current ? "page" : undefined}
                onClick={onNavigate}
              >
                {t(item.label)}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
