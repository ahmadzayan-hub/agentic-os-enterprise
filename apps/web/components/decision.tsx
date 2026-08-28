import Link from "next/link";

import { Empty, Status } from "@/components/ui";
import type {
  Confidence,
  DecisionOption,
  DecisionSummary,
  DecisionTransition,
} from "@/lib/types";

/**
 * Confidence, rendered.
 *
 * The one component in this codebase whose *absence* of a number is the point.
 * When the platform cannot defensibly compute a figure it says so in those
 * words, and shows why. A "—" would read as a rendering gap; a 0% would read
 * as a judgement.
 */
export function ConfidenceReadout({ confidence }: { confidence: Confidence }) {
  const calculated = confidence.value !== null;
  return (
    <div className="stack-sm">
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="stat-label">Confidence</span>
        <span
          className={calculated ? "stat-value" : "badge badge-muted"}
          style={calculated ? { fontSize: 22 } : undefined}
        >
          {confidence.display}
        </span>
      </div>
      {calculated ? (
        <details>
          <summary>How this was calculated</summary>
          <table>
            <caption className="visually-hidden">Confidence inputs</caption>
            <thead>
              <tr>
                <th scope="col">Input</th>
                <th scope="col" className="num">
                  Measured
                </th>
                <th scope="col" className="num">
                  Normalised
                </th>
                <th scope="col" className="num">
                  Weight
                </th>
              </tr>
            </thead>
            <tbody>
              {confidence.calculation.inputs.map((input) => (
                <tr key={input.name}>
                  <td>{input.name.replace(/_/g, " ")}</td>
                  <td className="num">{input.raw}</td>
                  <td className="num">{input.normalised}</td>
                  <td className="num">{input.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: 13 }}>
            Each input is normalised to 0–1 and multiplied by its weight. No part of
            this figure comes from a model&rsquo;s self-assessment.
          </p>
        </details>
      ) : (
        <p className="muted" style={{ fontSize: 13 }}>
          {confidence.calculation.reason ||
            "There is not enough evidence to compute a defensible figure."}{" "}
          A number has not been substituted.
        </p>
      )}
    </div>
  );
}

/** One row of the queue. Reference, state and domain lead, because that is
 *  what a reviewer scans for. */
export function DecisionRow({ decision }: { decision: DecisionSummary }) {
  return (
    <article className="card">
      <div className="card-head">
        <div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <Link href={`/decisions/${decision.id}`}>
              <strong>{decision.reference}</strong>
            </Link>
            <Status value={decision.state} />
            <Status value={decision.risk} />
          </div>
          <h3 style={{ margin: "6px 0 0", fontSize: 16 }}>
            <Link href={`/decisions/${decision.id}`}>{decision.title}</Link>
          </h3>
        </div>
        <span className="badge badge-muted">{decision.domain_name}</span>
      </div>
      {decision.summary ? <p style={{ marginBlockEnd: 0 }}>{decision.summary}</p> : null}
      <p className="muted" style={{ fontSize: 13, marginBlockEnd: 0 }}>
        Raised by {decision.detected_by.toLowerCase()}
        {decision.owner_email ? ` · owned by ${decision.owner_email}` : " · unowned"}
      </p>
    </article>
  );
}

/** The options table.
 *
 *  `is_status_quo` is called out deliberately: a decision presented without a
 *  do-nothing option is offering a false choice, and a reviewer should be able
 *  to see at a glance whether one was considered. */
export function OptionsTable({
  options,
  recommendedId,
}: {
  options: DecisionOption[];
  recommendedId: string | null;
}) {
  if (options.length === 0) {
    return <Empty>No options have been put forward yet.</Empty>;
  }
  return (
    <div className="table-wrap" tabIndex={0} role="group" aria-label="Options under consideration">
      <table>
        <caption className="visually-hidden">Options under consideration</caption>
        <thead>
          <tr>
            <th scope="col">Option</th>
            <th scope="col" className="num">
              Score
            </th>
            <th scope="col" className="num">
              Cost
            </th>
            <th scope="col">Risk</th>
            <th scope="col">Reversible</th>
          </tr>
        </thead>
        <tbody>
          {options.map((option) => (
            <tr key={option.id}>
              <td>
                <div className="row" style={{ gap: 6, alignItems: "center" }}>
                  <strong>{option.label}</strong>
                  {option.id === recommendedId ? (
                    <span className="badge badge-ok">Recommended</span>
                  ) : null}
                  {option.is_status_quo ? (
                    <span className="badge badge-muted">Do nothing</span>
                  ) : null}
                </div>
                {option.description ? (
                  <div className="muted" style={{ fontSize: 13 }}>
                    {option.description}
                  </div>
                ) : null}
              </td>
              <td className="num">
                {option.score === null ? <span className="muted">not scored</span> : option.score}
              </td>
              <td className="num">
                {option.estimated_cost === null ? (
                  <span className="muted">—</span>
                ) : (
                  `${option.currency} ${Number(option.estimated_cost).toLocaleString()}`
                )}
              </td>
              <td>
                <Status value={option.risk} />
              </td>
              <td>{option.reversible ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The case history. Append-only in the database, so this is the whole story
 *  and cannot have been tidied. */
export function History({ transitions }: { transitions: DecisionTransition[] }) {
  if (transitions.length === 0) return <Empty>No history recorded.</Empty>;
  return (
    <ol className="stack-sm" style={{ paddingInlineStart: 18 }}>
      {transitions.map((transition) => (
        <li key={transition.id}>
          <div className="row" style={{ gap: 6, alignItems: "center" }}>
            {transition.from_state ? (
              <span className="muted" style={{ fontSize: 13 }}>
                {transition.from_state} →
              </span>
            ) : (
              <span className="muted" style={{ fontSize: 13 }}>
                raised as
              </span>
            )}
            <Status value={transition.to_state} />
            <span className="badge badge-muted">{transition.actor_kind}</span>
          </div>
          {transition.reason ? (
            <div className="muted" style={{ fontSize: 13 }}>
              {transition.reason}
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
