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
