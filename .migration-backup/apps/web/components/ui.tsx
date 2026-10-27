import type { ReactNode } from "react";

/** Presentational primitives shared across every surface. */

export function Card({
  title,
  action,
  children,
  as: Element = "section",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  as?: "section" | "div" | "article";
}) {
  return (
    <Element className="card">
      {(title || action) && (
        <div className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {action}
        </div>
      )}
      {children}
    </Element>
  );
}

export function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
}) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {note ? <div className="stat-note">{note}</div> : null}
    </div>
  );
}

type Tone = "ok" | "warn" | "danger" | "info" | "muted";

const STATUS_TONE: Record<string, Tone> = {
  SUCCEEDED: "ok",
  APPROVED: "ok",
  VERIFIED: "ok",
  PRODUCTION_PROVEN: "ok",
  ALLOWED: "ok",
  ALLOW: "ok",
  ACTIVE: "ok",
  CLEAN: "ok",
  PUBLISHED: "ok",
  IMPLEMENTED: "ok",
  LOW: "ok",
  RUNNING: "info",
  PLANNING: "info",
  PENDING: "info",
  MONITOR: "info",
  INTERNAL: "info",
  AWAITING_APPROVAL: "warn",
  CHANGES_REQUESTED: "warn",
  REQUIRE_APPROVAL: "warn",
  MEDIUM: "warn",
  HIGH: "warn",
  EXPIRED: "warn",
  CONFIDENTIAL: "warn",
  NOT_EVIDENCED: "warn",
  NOT_IMPLEMENTED: "warn",
  FAILED: "danger",
  REJECTED: "danger",
  DENIED: "danger",
  DENY: "danger",
  CRITICAL: "danger",
  RESTRICTED: "danger",
  QUARANTINED: "danger",
  INFECTED: "danger",
};

/**
 * Status pill. Colour is reinforced by the label itself and by marker shape,
 * so meaning survives without colour perception.
 */
export function Status({ value, tone }: { value?: string | null; tone?: Tone }) {
  if (!value) return <span className="muted">—</span>;
  const resolved = tone ?? STATUS_TONE[value.toUpperCase()] ?? "muted";
  return <span className={`badge badge-${resolved}`}>{value}</span>;
}

export function Meter({
  value,
  max = 100,
  label,
}: {
  value: number;
  max?: number;
  label: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const tone = pct >= 90 ? "is-ok" : pct >= 70 ? "" : pct >= 40 ? "is-warn" : "is-danger";
  return (
    <div
      role="meter"
      aria-valuenow={Number(value.toFixed(1))}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
      className="meter"
    >
      <div className={`meter-fill ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "danger" | "plain";
  children: ReactNode;
}) {
  const cls = tone === "plain" ? "notice" : `notice notice-${tone}`;
  return (
    <div className={cls} role={tone === "danger" ? "alert" : undefined}>
      {children}
    </div>
  );
}

/** Renders a fetch failure without pretending the surface has no data. */
export function SurfaceError({
  error,
  status,
  what,
}: {
  error: string;
  status: number;
  what: string;
}) {
  const forbidden = status === 403;
  return (
    <Notice tone={forbidden ? "warn" : "danger"}>
      <strong>{forbidden ? "Not permitted" : "Unavailable"}</strong>
      {": "}
      {forbidden
        ? `Your role does not grant access to ${what}.`
        : `${what} could not be loaded — ${error}`}
    </Notice>
  );
}

export function DataTable({
  caption,
  columns,
  rows,
  empty = "No records.",
}: {
  caption: string;
  columns: { key: string; label: string; numeric?: boolean; hideLabel?: boolean }[];
  rows: Record<string, ReactNode>[];
  empty?: string;
}) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    // A horizontally scrollable region must be keyboard reachable, so it takes
    // focus and is labelled by the table's own caption.
    <div className="table-wrap" tabIndex={0} role="group" aria-label={caption}>
      <table>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              // A column of controls still needs a header a screen reader can
              // announce. Leaving the label empty is the obvious thing and it is
              // an axe violation (empty-table-header) — `hideLabel` keeps the
              // column visually bare while still naming it.
              <th
                key={column.key}
                scope="col"
                className={[column.numeric ? "num" : "", column.hideLabel ? "visually-hidden" : ""]
                  .filter(Boolean)
                  .join(" ")}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.__key ?? index)}>
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "num" : undefined}>
                  {row[column.key] ?? <span className="muted">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="mono">{children}</span>;
}

export function formatWhen(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

export function formatCost(usd?: number | null): string {
  if (usd === null || usd === undefined) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(6)}`;
  return `$${usd.toFixed(2)}`;
}
