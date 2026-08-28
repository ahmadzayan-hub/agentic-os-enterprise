import { notFound } from "next/navigation";

import { ConfidenceReadout, History, OptionsTable } from "@/components/decision";
import { Card, Empty, Notice, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { DecisionCase, LifecycleGraph } from "@/lib/types";

/**
 * One decision case.
 *
 * Everything a reviewer needs in order to decide, on one page: what was
 * detected, what options exist, what evidence supports them, what is
 * recommended and how confident that recommendation defensibly is, what has
 * already happened, and — once it is done — whether it worked.
 *
 * The available moves are read from the API's own lifecycle graph rather than
 * duplicated here. A second copy of a state machine is a second state machine,
 * and it starts disagreeing with the first one on the day somebody edits only
 * one of them.
 */
export default async function DecisionCasePage({
  params,
  searchParams,
}: {
  params: Promise<{ decisionId: string }>;
  searchParams: Promise<{ error?: string; moved?: string }>;
}) {
  const { decisionId } = await params;
  const query = await searchParams;

  const [caseResult, graphResult] = await Promise.all([
    apiTry<DecisionCase>(`/api/v1/decisions/${decisionId}`),
    apiTry<LifecycleGraph>("/api/v1/decisions/states"),
  ]);

  // A decision outside the caller's domain is genuinely not found here, which
  // is the same answer the API gives and for the same reason.
  if (caseResult.status === 404) notFound();
  if (!caseResult.data) {
    return (
      <SurfaceError
        error={caseResult.error ?? ""}
        status={caseResult.status}
        what="this decision"
      />
    );
  }

  const decision = caseResult.data;
  const nextStates = graphResult.data?.transitions[decision.state] ?? [];
  const outcome = decision.outcomes[0] ?? null;

  return (
    <div className="stack">
      <div>
        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <span className="badge badge-muted">{decision.reference}</span>
          <Status value={decision.state} />
          <Status value={decision.risk} />
          <Status value={decision.classification} />
          <span className="badge badge-muted">{decision.domain_name}</span>
        </div>
        <h1 style={{ marginBlockStart: 8 }}>{decision.title}</h1>
        {decision.summary ? <p className="page-lede">{decision.summary}</p> : null}
      </div>

      {query.moved ? <Notice tone="info">The decision moved to {query.moved}.</Notice> : null}
      {query.error ? <Notice tone="danger">{query.error}</Notice> : null}

      <div className="grid-2">
        <Card title="Recommendation">
          {decision.recommendation ? (
            <div className="stack-sm">
              <ConfidenceReadout confidence={decision.confidence} />
              {decision.recommendation.reasoning_summary ? (
                <div>
                  <h3 style={{ fontSize: 14, marginBlockEnd: 4 }}>Reasoning</h3>
                  <p style={{ marginBlockEnd: 0 }}>
                    {decision.recommendation.reasoning_summary}
                  </p>
                </div>
              ) : null}
              {decision.recommendation.rationale ? (
                <div>
                  <h3 style={{ fontSize: 14, marginBlockEnd: 4 }}>Why this option</h3>
                  <p style={{ marginBlockEnd: 0 }}>{decision.recommendation.rationale}</p>
                </div>
              ) : null}
              <p className="muted" style={{ fontSize: 13, marginBlockEnd: 0 }}>
                Produced by {decision.recommendation.produced_by.toLowerCase()}.
              </p>
            </div>
          ) : (
            <Empty>
              No recommendation yet. This case is still being analysed, and nothing
              has been put to a reviewer.
            </Empty>
          )}
        </Card>

        <Card title="How it was detected">
          <dl className="kv">
            <dt>Detected by</dt>
            <dd>{decision.detected_by}</dd>
            <dt>Source</dt>
            <dd>{decision.detection_source || <span className="muted">—</span>}</dd>
            <dt>Owner</dt>
            <dd>{decision.owner_email ?? <span className="muted">unassigned</span>}</dd>
            <dt>Raised</dt>
            <dd>{formatWhen(decision.created_at)}</dd>
            <dt>Due</dt>
            <dd>{decision.due_at ? formatWhen(decision.due_at) : <span className="muted">—</span>}</dd>
          </dl>
        </Card>
      </div>

      <Card title="Options">
        <OptionsTable
          options={decision.options}
          recommendedId={decision.recommendation?.option_id ?? null}
        />
      </Card>

      <Card title={`Evidence (${decision.evidence.length})`}>
        {decision.evidence.length === 0 ? (
          <Empty>
            Nothing has been cited. Confidence cannot be calculated without evidence,
            and this case reports that rather than guessing.
          </Empty>
        ) : (
          <div className="table-wrap" tabIndex={0} role="group" aria-label="Evidence cited">
            <table>
              <caption className="visually-hidden">Evidence cited</caption>
              <thead>
                <tr>
                  <th scope="col">Kind</th>
                  <th scope="col">What it shows</th>
                  <th scope="col" className="num">
                    Authority
                  </th>
                  <th scope="col">Observed</th>
                </tr>
              </thead>
              <tbody>
                {decision.evidence.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <span className="badge badge-muted">{item.source_kind}</span>
                    </td>
                    <td>
                      {item.summary}
                      {item.source_ref ? (
                        <div className="muted" style={{ fontSize: 13 }}>
                          {item.source_ref}
                        </div>
                      ) : null}
                    </td>
                    <td className="num">{item.authority_weight}</td>
                    <td>{formatWhen(item.observed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid-2">
        <Card title="History">
          <History transitions={decision.transitions} />
        </Card>

        <Card title="Outcome">
          {outcome ? (
            <div className="stack-sm">
              <div className="row" style={{ gap: 8, alignItems: "center" }}>
                <Status value={outcome.verdict} />
                {outcome.verified_at ? (
                  <span className="muted" style={{ fontSize: 13 }}>
                    verified {formatWhen(outcome.verified_at)}
                  </span>
                ) : null}
              </div>
              <dl className="kv">
                <dt>Target</dt>
                <dd>
                  {outcome.target_value ?? <span className="muted">—</span>} {outcome.unit}
                </dd>
                <dt>Actual</dt>
                <dd>
                  {outcome.actual_value ?? <span className="muted">—</span>} {outcome.unit}
                </dd>
                <dt>How it was checked</dt>
                <dd>{outcome.verification_method}</dd>
              </dl>
            </div>
          ) : (
            <Empty>
              No outcome recorded. Until one is, this decision counts towards neither
              success nor failure in the effectiveness rate.
            </Empty>
          )}
        </Card>
      </div>

      {decision.lessons.length > 0 ? (
        <Card title="What we learned">
          <ul className="stack-sm" style={{ paddingInlineStart: 18 }}>
            {decision.lessons.map((lesson) => (
              <li key={lesson.id}>
                <span className="badge badge-muted">{lesson.category}</span> {lesson.lesson}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <Card title="Move this decision">
        {nextStates.length === 0 ? (
          <Empty>This case is closed. No further moves are possible.</Empty>
        ) : (
          <form
            action={`/api/decisions/${decision.id}/transitions`}
            method="post"
            className="stack-sm"
          >
            <p className="muted" style={{ fontSize: 13 }}>
              Only the moves the lifecycle permits are offered, and the server checks
              your permission again regardless of what this form sends.
            </p>
            <label htmlFor="to_state">Move to</label>
            <select id="to_state" name="to_state" required>
              {nextStates.map((state) => (
                <option key={state} value={state}>
                  {state.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <label htmlFor="reason">Reason</label>
            <textarea id="reason" name="reason" rows={2} maxLength={2000} />
            <button className="btn btn-primary" type="submit">
              Record decision
            </button>
          </form>
        )}
      </Card>
    </div>
  );
}
