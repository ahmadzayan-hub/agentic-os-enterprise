/**
 * Display rules for the decision surfaces.
 *
 * These live here rather than inline in JSX for one reason: inline in a
 * component they cannot be tested. The console's only automated checks were a
 * build and an axe audit, neither of which can tell the difference between
 * "Not Calculated" and "0%" — and that difference is the single most important
 * thing this product renders.
 *
 * Everything in this module is a pure function of its arguments, so it can be
 * exercised by `node --test` without a DOM, a renderer, or a running API.
 */

/**
 * The words shown when the platform will not put a number on something.
 *
 * A constant rather than a literal repeated across three components: the
 * phrase is a contract with the reader, and a component that drifted to
 * "Unknown" or "—" would break it silently.
 */
export const NOT_CALCULATED = "Not Calculated";

export interface ConfidenceLike {
  value: number | null;
  calculation?: { reason?: string };
}

/**
 * How a confidence renders.
 *
 * `null` is not missing data to be papered over — it is the platform declining
 * to assert something it cannot support. Zero would read as "certainly wrong"
 * and a dash would read as a rendering fault, so neither is acceptable.
 */
export function confidenceLabel(confidence: ConfidenceLike): string {
  if (confidence.value === null) return NOT_CALCULATED;
  return `${Math.round(confidence.value * 100)}%`;
}

/** Why a confidence is absent, in a sentence a reviewer can act on. */
export function confidenceReason(confidence: ConfidenceLike): string {
  if (confidence.value !== null) return "";
  return (
    confidence.calculation?.reason ||
    "There is not enough evidence to compute a defensible figure."
  );
}

/**
 * Round a measurement for display.
 *
 * The stored value keeps full precision so its computation record can be
 * reconstructed; 87.610619% on an executive surface reads as false precision,
 * implying the sixth decimal means something. Two decimals, trailing zeros
 * dropped so a clean figure stays clean.
 */
export function formatMeasurement(value: number): string {
  return Number(value.toFixed(2)).toLocaleString("en-US");
}

export interface KpiLike {
  latest_value: number | null;
  unit: string;
  computation: "REGISTERED" | "NO_COMPUTATION";
}

/**
 * What a KPI shows, including *why* it shows nothing.
 *
 * Two different facts hide behind an absent value, and a reader acts
 * differently on each: "nothing happened this period" is a prompt to wait,
 * "the platform cannot measure this" is a prompt to go and build a data feed.
 * Rendering both as a dash loses that distinction.
 */
export function kpiValueLabel(kpi: KpiLike): string {
  if (kpi.latest_value !== null) {
    return `${formatMeasurement(kpi.latest_value)} ${kpi.unit}`.trim();
  }
  return kpi.computation === "NO_COMPUTATION"
    ? "Not measurable — this KPI is defined, and the platform holds no data it can be computed from"
    : "Not measured yet — nothing to measure in the current period";
}

/** Effectiveness, with the same refusal to invent a number over an empty set. */
export function effectivenessLabel(rate: { rate: number | null } | null): string {
  if (!rate || rate.rate === null) return NOT_CALCULATED;
  return `${Math.round(rate.rate * 100)}%`;
}

/** Where a decision sits in the queue. */
export type Bucket = "WAITING_ON_A_PERSON" | "IN_PROGRESS" | "SETTLED";

const AWAITING_A_PERSON = new Set([
  "AWAITING_REVIEW",
  "AWAITING_APPROVAL",
  "VERIFICATION_PENDING",
]);

const IN_PROGRESS = new Set([
  "DETECTED",
  "ANALYSING",
  "RECOMMENDATION_READY",
  "APPROVED",
  "EXECUTING",
]);

/**
 * Which section of the queue a decision belongs to.
 *
 * An unrecognised state falls to SETTLED rather than being dropped. A case the
 * console does not understand must still appear somewhere: silently vanishing
 * from every bucket is how work goes missing.
 */
export function queueBucket(state: string): Bucket {
  if (AWAITING_A_PERSON.has(state)) return "WAITING_ON_A_PERSON";
  if (IN_PROGRESS.has(state)) return "IN_PROGRESS";
  return "SETTLED";
}

/**
 * How many times a condition has recurred, in words.
 *
 * The count is the difference between "this happened" and "this has been
 * happening all week", and a bare integer in a column reads as neither. One
 * occurrence must not render as "1 times", and — more importantly — must not
 * be silently dropped, because an alert with no count beside it looks like an
 * alert that only just started.
 */
export function occurrenceLabel(count: number): string {
  if (count <= 1) return "Seen once";
  return `Seen ${count.toLocaleString("en-US")} times`;
}

/**
 * Escalation, which is a claim that nobody has answered.
 *
 * Level 0 is the common case and says something real — it was raised and the
 * clock has not run out — so it gets words rather than a blank cell.
 */
export function escalationLabel(level: number): string {
  if (level <= 0) return "Not escalated";
  return level === 1 ? "Escalated once" : `Escalated ${level} times`;
}

/**
 * Who owns an alert.
 *
 * An unassigned alert must say so. A blank owner column reads as a rendering
 * gap and an alert nobody is named against is the one most likely to be missed
 * — the platform could find nobody holding the required permission inside the
 * relevant domain, and that is a staffing fact worth putting on the screen
 * rather than an empty cell.
 */
export function alertOwnerLabel(email: string | null): string {
  return email ?? "Unassigned — nobody holds the permission this alert needs";
}

/**
 * What the alert list is telling you overall.
 *
 * Zero open alerts is reported as a measurement, not as silence: "nothing is
 * open" and "alerting has never run" look identical on a quiet screen, and
 * only one of them is good news.
 */
export function alertSummary(counts: {
  total: number;
  open: number;
  critical_open: number;
  unassigned: number;
}): string {
  if (counts.total === 0) {
    return "No alert has ever been raised in your domains.";
  }
  if (counts.open === 0) {
    return `Nothing is open. ${counts.total.toLocaleString("en-US")} alerts have been raised and resolved.`;
  }
  const parts = [`${counts.open.toLocaleString("en-US")} open`];
  if (counts.critical_open > 0) parts.push(`${counts.critical_open} critical`);
  if (counts.unassigned > 0) parts.push(`${counts.unassigned} with nobody assigned`);
  return `${parts.join(", ")}.`;
}
