/**
 * The console's display contract.
 *
 * Until now the frontend had a build and an axe audit and nothing else. Neither
 * can tell the difference between "Not Calculated" and "0%", which is the
 * single most consequential distinction this product renders: one says the
 * platform declines to assert a figure, the other says it measured and the
 * answer was nought.
 *
 * Run with Node's own test runner and its native type stripping, so this costs
 * no new dependency:
 *
 *     npm test
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  NOT_CALCULATED,
  alertOwnerLabel,
  alertSummary,
  confidenceLabel,
  confidenceReason,
  effectivenessLabel,
  escalationLabel,
  formatMeasurement,
  kpiValueLabel,
  occurrenceLabel,
  queueBucket,
} from "../../lib/display.ts";

describe("confidence", () => {
  it("renders an absent figure as the words, never as a number", () => {
    assert.equal(confidenceLabel({ value: null }), "Not Calculated");
  });

  it("never renders an absent figure as zero", () => {
    // The failure mode worth naming: 0% reads as "certainly wrong", which is a
    // claim, where null is the refusal to make one.
    const label = confidenceLabel({ value: null });
    assert.ok(!label.includes("0"), `absent confidence rendered as "${label}"`);
    assert.ok(!label.includes("%"), `absent confidence rendered as a percentage`);
  });

  it("never renders an absent figure as a dash, which reads as a fault", () => {
    assert.ok(!confidenceLabel({ value: null }).includes("—"));
  });

  it("renders a real figure as a whole percentage", () => {
    assert.equal(confidenceLabel({ value: 0.67 }), "67%");
    assert.equal(confidenceLabel({ value: 1 }), "100%");
  });

  it("distinguishes a genuine zero from an absent figure", () => {
    // A computed 0.0 is a measurement and must render as one.
    assert.equal(confidenceLabel({ value: 0 }), "0%");
    assert.notEqual(confidenceLabel({ value: 0 }), confidenceLabel({ value: null }));
  });

  it("gives the reason a figure is missing", () => {
    assert.equal(
      confidenceReason({ value: null, calculation: { reason: "no evidence is linked" } }),
      "no evidence is linked",
    );
  });

  it("falls back to a stated reason rather than an empty explanation", () => {
    assert.ok(confidenceReason({ value: null }).length > 20);
  });

  it("offers no reason when there is a figure", () => {
    assert.equal(confidenceReason({ value: 0.5 }), "");
  });
});

describe("measurements", () => {
  it("does not leak false precision onto an executive surface", () => {
    assert.equal(formatMeasurement(87.610619), "87.61");
  });

  it("keeps a clean figure clean", () => {
    assert.equal(formatMeasurement(100), "100");
    assert.equal(formatMeasurement(99.5), "99.5");
  });

  it("rounds rather than truncates", () => {
    assert.equal(formatMeasurement(2.005), "2");
    assert.equal(formatMeasurement(2.006), "2.01");
  });

  it("handles zero without turning it into something else", () => {
    assert.equal(formatMeasurement(0), "0");
  });
});

describe("KPI values", () => {
  it("distinguishes an unmeasured period from an unmeasurable KPI", () => {
    const unmeasured = kpiValueLabel({
      latest_value: null,
      unit: "%",
      computation: "REGISTERED",
    });
    const unmeasurable = kpiValueLabel({
      latest_value: null,
      unit: "%",
      computation: "NO_COMPUTATION",
    });
    assert.notEqual(unmeasured, unmeasurable);
    assert.ok(unmeasured.includes("current period"));
    assert.ok(unmeasurable.includes("no data"));
  });

  it("never shows zero for an absent value", () => {
    for (const computation of ["REGISTERED", "NO_COMPUTATION"] as const) {
      const label = kpiValueLabel({ latest_value: null, unit: "%", computation });
      assert.ok(!/\b0\b/.test(label), `absent KPI rendered as "${label}"`);
    }
  });

  it("shows a measured value with its unit", () => {
    assert.equal(
      kpiValueLabel({ latest_value: 87.610619, unit: "%", computation: "REGISTERED" }),
      "87.61 %",
    );
  });

  it("shows a measured zero, which is a measurement", () => {
    assert.equal(
      kpiValueLabel({ latest_value: 0, unit: "failures", computation: "REGISTERED" }),
      "0 failures",
    );
  });
});

describe("effectiveness", () => {
  it("declines to report a rate over an empty set", () => {
    assert.equal(effectivenessLabel({ rate: null }), NOT_CALCULATED);
    assert.equal(effectivenessLabel(null), NOT_CALCULATED);
  });

  it("does not report an empty set as either total failure or total success", () => {
    const label = effectivenessLabel({ rate: null });
    assert.notEqual(label, "0%");
    assert.notEqual(label, "100%");
  });

  it("reports a real rate", () => {
    assert.equal(effectivenessLabel({ rate: 0.5 }), "50%");
    assert.equal(effectivenessLabel({ rate: 1 }), "100%");
  });
});

describe("the decision queue", () => {
  it("puts the states a person is blocking on first", () => {
    for (const state of ["AWAITING_REVIEW", "AWAITING_APPROVAL", "VERIFICATION_PENDING"]) {
      assert.equal(queueBucket(state), "WAITING_ON_A_PERSON", state);
    }
  });

  it("separates work in flight from work that is settled", () => {
    assert.equal(queueBucket("ANALYSING"), "IN_PROGRESS");
    assert.equal(queueBucket("EXECUTING"), "IN_PROGRESS");
    assert.equal(queueBucket("VERIFIED"), "SETTLED");
    assert.equal(queueBucket("CLOSED"), "SETTLED");
    assert.equal(queueBucket("REJECTED"), "SETTLED");
  });

  it("never drops a state it does not recognise", () => {
    // A case the console cannot classify must still appear somewhere.
    // Vanishing from every bucket is how work goes missing.
    assert.equal(queueBucket("SOME_FUTURE_STATE"), "SETTLED");
    assert.equal(queueBucket(""), "SETTLED");
  });

  it("assigns every state in the lifecycle to exactly one bucket", () => {
    const lifecycle = [
      "DETECTED",
      "ANALYSING",
      "RECOMMENDATION_READY",
      "AWAITING_REVIEW",
      "AWAITING_APPROVAL",
      "APPROVED",
      "REJECTED",
      "EXECUTING",
      "VERIFICATION_PENDING",
      "VERIFIED",
      "CLOSED",
    ];
    const buckets = lifecycle.map(queueBucket);
    assert.equal(buckets.length, 11);
    assert.ok(buckets.every((b) => b !== undefined));
    // All three sections are used; a bucket nothing reaches is dead UI.
    assert.equal(new Set(buckets).size, 3);
  });
});

describe("alerts", () => {
  it("does not render a single occurrence as \"1 times\"", () => {
    assert.equal(occurrenceLabel(1), "Seen once");
    assert.equal(occurrenceLabel(0), "Seen once");
  });

  it("makes a recurring condition read as recurring", () => {
    assert.equal(occurrenceLabel(14), "Seen 14 times");
    assert.notEqual(occurrenceLabel(14), occurrenceLabel(1));
  });

  it("says an alert is not escalated rather than showing nothing", () => {
    const label = escalationLabel(0);
    assert.ok(label.length > 0);
    assert.ok(!label.includes("0"));
  });

  it("counts escalations without inventing a plural", () => {
    assert.equal(escalationLabel(1), "Escalated once");
    assert.equal(escalationLabel(3), "Escalated 3 times");
  });

  it("never leaves an unassigned alert looking blank", () => {
    // A blank owner cell reads as a rendering fault, and the alert nobody is
    // named against is the one most likely to be missed.
    const label = alertOwnerLabel(null);
    assert.ok(label.length > 10);
    assert.ok(label.toLowerCase().includes("unassigned"));
  });

  it("shows a real owner as themselves", () => {
    assert.equal(alertOwnerLabel("lead@rta.example"), "lead@rta.example");
  });

  it("distinguishes a quiet system from one that has never alerted", () => {
    const never = alertSummary({ total: 0, open: 0, critical_open: 0, unassigned: 0 });
    const quiet = alertSummary({ total: 9, open: 0, critical_open: 0, unassigned: 0 });
    assert.notEqual(never, quiet);
    assert.ok(never.includes("ever been raised"));
    assert.ok(quiet.includes("resolved"));
  });

  it("leads with what is open, and names what nobody owns", () => {
    const summary = alertSummary({ total: 12, open: 4, critical_open: 1, unassigned: 2 });
    assert.ok(summary.startsWith("4 open"));
    assert.ok(summary.includes("1 critical"));
    assert.ok(summary.includes("nobody assigned"));
  });

  it("does not mention critical or unassigned when there are none", () => {
    const summary = alertSummary({ total: 5, open: 2, critical_open: 0, unassigned: 0 });
    assert.equal(summary, "2 open.");
  });
});
