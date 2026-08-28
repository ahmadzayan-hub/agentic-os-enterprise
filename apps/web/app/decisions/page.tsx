import { DecisionRow } from "@/components/decision";
import { Card, Empty, Notice, SurfaceError } from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { DecisionQueue, DecisionSummary } from "@/lib/types";

export const metadata = { title: "Decision Queue" };

/**
 * The Decision Workbench queue.
 *
 * Organised around what a person has to do next, not around the tables the
 * data happens to live in. Cases awaiting a human are shown first because a
 * queue whose top item is not the most urgent one is a list, not a queue.
 *
 * The scope of the queue is stated on the page. A surface that silently
 * shows a subset is worse than one that says it is showing a subset: the
 * reader has no way to tell the difference between "nothing is waiting" and
 * "nothing you can see is waiting".
 */

const AWAITING_A_PERSON = ["AWAITING_REVIEW", "AWAITING_APPROVAL", "VERIFICATION_PENDING"];
const IN_PROGRESS = ["DETECTED", "ANALYSING", "RECOMMENDATION_READY", "APPROVED", "EXECUTING"];

export default async function DecisionsPage() {
  const { data, error, status } = await apiTry<DecisionQueue>("/api/v1/decisions?limit=200");

  if (!data) {
    return (
      <div className="stack">
        <Header />
        <SurfaceError error={error ?? ""} status={status} what="the decision queue" />
      </div>
    );
  }

  const waiting = data.items.filter((d) => AWAITING_A_PERSON.includes(d.state));
  const active = data.items.filter((d) => IN_PROGRESS.includes(d.state));
  const settled = data.items.filter(
    (d) => !AWAITING_A_PERSON.includes(d.state) && !IN_PROGRESS.includes(d.state),
  );

  return (
    <div className="stack">
      <Header />

      <Notice tone="plain">
        {data.scope.sees_all_domains ? (
          <>
            You hold a cross-domain role, so this queue covers every domain in the
            tenant.
          </>
        ) : (
          <>
            This queue covers the {data.scope.domains.length}{" "}
            {data.scope.domains.length === 1 ? "domain" : "domains"} you belong to.
            Decisions in other domains are not listed and cannot be opened.
          </>
        )}
      </Notice>

      <Section
        title={`Waiting on a person (${waiting.length})`}
        decisions={waiting}
        empty={
          data.scope.domains.length === 0 && !data.scope.sees_all_domains
            ? "You do not belong to any domain yet, so no decisions are visible to you. Ask an administrator to add you to a team."
            : "Nothing is waiting on a human decision right now."
        }
      />
      <Section
        title={`In progress (${active.length})`}
        decisions={active}
        empty="No cases are currently being analysed or executed."
      />
      <Section
        title={`Settled (${settled.length})`}
        decisions={settled}
        empty="No decisions have been closed or verified yet."
      />
    </div>
  );
}

function Header() {
  return (
    <div>
      <h1>Decision Queue</h1>
      <p className="page-lede">
        Every decision the organisation is currently making, and what each one is
        waiting for. A case moves only through the states its lifecycle permits,
        and every move is recorded where it cannot be edited.
      </p>
    </div>
  );
}

function Section({
  title,
  decisions,
  empty,
}: {
  title: string;
  decisions: DecisionSummary[];
  empty: string;
}) {
  return (
    <section className="stack-sm">
      <h2>{title}</h2>
      {decisions.length === 0 ? (
        <Card>
          <Empty>{empty}</Empty>
        </Card>
      ) : (
        decisions.map((decision) => <DecisionRow key={decision.id} decision={decision} />)
      )}
    </section>
  );
}
