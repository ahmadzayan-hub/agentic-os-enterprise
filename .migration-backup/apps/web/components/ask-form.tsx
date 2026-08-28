"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Ask Agentic OS — the natural-language entry point.
 *
 * Submitting runs the full governed path: intent, plan, validation, risk,
 * policy, approval where required, then dispatch. The form reports the run's
 * real outcome, including refusals, rather than pretending everything succeeds.
 */
export function AskForm() {
  const router = useRouter();
  const [objective, setObjective] = useState("");
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null);
  const [pending, startTransition] = useTransition();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (objective.trim().length < 3) return;
    setMessage(null);

    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ objective }),
    });
    const body = await response.json().catch(() => null);

    if (!response.ok) {
      setMessage({
        tone: "danger",
        text: body?.message ?? `Request refused (${response.status}).`,
      });
      return;
    }

    if (body.status === "AWAITING_APPROVAL") {
      setMessage({
        tone: "warn",
        text: "The plan requires human authorisation. It is waiting in Approvals.",
      });
    } else if (body.status === "FAILED") {
      const issues: string[] = (body.validation?.issues ?? []).map(
        (issue: { code: string; message: string }) => `${issue.code}: ${issue.message}`,
      );
      setMessage({
        tone: "danger",
        text: issues.length
          ? `The plan was rejected before execution — ${issues[0]}`
          : body.error_message || "The run failed.",
      });
    } else {
      setObjective("");
      setMessage({ tone: "ok", text: "Run completed." });
    }

    startTransition(() => {
      router.push(`/runs/${body.run_id}`);
      router.refresh();
    });
  }

  return (
    <section className="card" aria-labelledby="ask-heading">
      <h2 id="ask-heading">Ask Agentic OS</h2>
      <p className="muted" style={{ margin: "0 0 12px", fontSize: 13 }}>
        State an objective. The Conductor plans it, the validator gates it, and only
        an authorised agent executes it.
      </p>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="objective" className="visually-hidden">
            Objective
          </label>
          <textarea
            id="objective"
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
            placeholder="Which escalator failures are recurring, and what is the dominant failure mode?"
            aria-describedby="ask-hint"
            required
            minLength={3}
            maxLength={4000}
          />
          <p className="field-hint" id="ask-hint">
            Consequential objectives are routed to human approval rather than executed.
          </p>
        </div>
        <div className="row">
          <button className="btn btn-primary" type="submit" disabled={pending}>
            {pending ? "Submitting…" : "Run"}
          </button>
          {message ? (
            <span
              className={`badge badge-${message.tone}`}
              role="status"
              aria-live="polite"
            >
              {message.text}
            </span>
          ) : null}
        </div>
      </form>
    </section>
  );
}
