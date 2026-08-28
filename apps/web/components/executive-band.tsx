import Link from "next/link";

import { Card, Empty, Stat, Status } from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { DecisionQueue, Effectiveness, KpiDefinition } from "@/lib/types";

/**
 * The executive band: what the organisation is deciding, and whether its
 * decisions work.
 *
 * This sits above platform health on the landing surface, and the ordering is
 * the argument. Run counts and control scores answer "is the platform
 * healthy?"; an executive opens the product to ask "what needs me, and did
 * what we did last quarter work?". The platform question is real and stays on
 * the page — below this.
 *
 * Every figure here can be absent, and absence is rendered as the words
 * "Not Calculated" rather than as a zero.
 */

const AWAITING_A_PERSON = ["AWAITING_REVIEW", "AWAITING_APPROVAL", "VERIFICATION_PENDING"];

/**
 * Round a measurement for display.
 *
 * The stored value keeps full precision because the computation record has to
 * reconstruct it, but 87.610619% on an executive surface reads as false
 * precision — it implies the sixth decimal means something. Two decimals, and
 * trailing zeros dropped so a clean figure stays clean.
 */
function formatMeasurement(value: number): string {
  return Number(value.toFixed(2)).toLocaleString();
}

export async function ExecutiveBand() {
  const [queue, effectiveness, kpis] = await Promise.all([
    apiTry<DecisionQueue>("/api/v1/decisions?limit=200"),
    apiTry<Effectiveness>("/api/v1/decisions/effectiveness"),
    apiTry<{ items: KpiDefinition[]; measurable: number; unmeasurable: number }>(
      "/api/v1/kpis",
    ),
  ]);

  // A viewer without decisions:read is not shown a broken panel; the surface
  // simply does not claim to cover something it cannot see.
  if (!queue.data) return null;

  const waiting = queue.data.items.filter((d) => AWAITING_A_PERSON.includes(d.state));
  const rate = effectiveness.data;

  return (
    <section aria-labelledby="decisions-heading" className="stack">
      <h2 id="decisions-heading">Decisions</h2>

      <div className="grid grid-3">
        <Stat
          label="Decision effectiveness"
          value={rate ? rate.display : "Not Calculated"}
          note={
            rate && rate.rate !== null ? (
              <>
                {rate.achieved} of {rate.reached_verification} verified decisions achieved
                their intended outcome
              </>
            ) : (
              <>
                No decision has reached verification yet, so there is no rate — this is
                not a rate of zero
              </>
            )
          }
        />
        <Stat
          label="Waiting on a person"
          value={waiting.length}
          note={<Link href="/decisions">Open the decision queue</Link>}
        />
        <Stat
          label="In flight"
          value={rate ? rate.in_flight : "—"}
          note="Detected, being analysed, or executing"
        />
      </div>

      <div className="grid grid-2">
        <Card title="Needs a human decision" action={<Link href="/decisions">All decisions</Link>}>
          {waiting.length === 0 ? (
            <Empty>
              Nothing is waiting on a person in the domains you can see.
            </Empty>
          ) : (
            <ul className="stack-sm" style={{ paddingInlineStart: 18 }}>
              {waiting.slice(0, 6).map((decision) => (
                <li key={decision.id}>
                  <div className="row" style={{ gap: 8, alignItems: "center" }}>
                    <Link href={`/decisions/${decision.id}`}>{decision.reference}</Link>
                    <Status value={decision.state} />
                    <Status value={decision.risk} />
                  </div>
                  <div style={{ fontSize: 14 }}>{decision.title}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Key performance indicators">
          {!kpis.data || kpis.data.items.length === 0 ? (
            <Empty>No KPIs have been defined for your domains.</Empty>
          ) : (
            <>
              {kpis.data.unmeasurable > 0 ? (
                <p className="muted" style={{ fontSize: 13, marginBlockStart: 0 }}>
                  {kpis.data.measurable} of {kpis.data.items.length} are measurable from
                  data the platform holds. The rest are defined and unmeasured.
                </p>
              ) : null}
            <ul className="stack-sm" style={{ paddingInlineStart: 18 }}>
              {kpis.data.items.map((kpi) => (
                <li key={kpi.id}>
                  <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
                    <strong>{kpi.name}</strong>
                    <span className="badge badge-muted">
                      {kpi.direction === "UP_IS_GOOD" ? "higher is better" : "lower is better"}
                    </span>
                  </div>
                  <div style={{ fontSize: 14 }}>
                    {kpi.latest_value === null ? (
                      <span className="muted">
                        {kpi.computation === "NO_COMPUTATION"
                          ? "Not measurable — this KPI is defined, and the platform holds no data it can be computed from"
                          : "Not measured yet — nothing to measure in the current period"}
                      </span>
                    ) : (
                      <>
                        {formatMeasurement(kpi.latest_value)} {kpi.unit}
                        {kpi.target_value !== null ? (
                          <span className="muted">
                            {" "}
                            against a target of {kpi.target_value} {kpi.unit}
                          </span>
                        ) : null}
                      </>
                    )}
                  </div>
                  <div className="muted" style={{ fontSize: 13 }}>
                    {kpi.formula}
                  </div>
                </li>
              ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    </section>
  );
}
