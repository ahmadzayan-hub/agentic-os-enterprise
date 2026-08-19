import { Card, DataTable, Meter, Notice, Stat, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { MaturityReport } from "@/lib/types";

/**
 * The maturity surface reads the Evidence Engine's derived report. No number on
 * this page can be typed in by a human: each control's status comes from whether
 * its named automated test passed in the run being assessed.
 */
export default async function EvidencePage() {
  const { data, error, status } = await apiTry<MaturityReport>("/api/v1/evidence");

  if (!data) {
    return (
      <>
        <h1>Evidence</h1>
        <SurfaceError error={error ?? ""} status={status} what="the maturity report" />
      </>
    );
  }

  if (!data.available) {
    return (
      <div className="stack">
        <h1>Evidence</h1>
        <Notice tone="warn">
          {data.message ??
            "No evidence has been collected. Maturity is derived from a test run, so there is nothing to report until one has happened."}
        </Notice>
      </div>
    );
  }

  const unverified = data.controls.filter(
    (control) => !["VERIFIED", "PRODUCTION_PROVEN"].includes(control.status),
  );

  return (
    <div className="stack">
      <div>
        <h1>Evidence and maturity</h1>
        <p className="page-lede">
          Every figure here is derived from the test suite. A control whose named
          test did not pass cannot reach VERIFIED, and no control status can be set
          by hand.
        </p>
      </div>

      <div className="grid grid-4">
        <Stat
          label="Maturity"
          value={`${data.score.toFixed(1)}`}
          note={`of 100 · ${data.environment}`}
        />
        <Stat
          label="Certified"
          value={data.certified ? "Yes" : "No"}
          note={
            data.certified
              ? "all applicable controls verified"
              : "certification requires 100 and no blockers"
          }
        />
        <Stat
          label="Critical blockers"
          value={data.critical_blockers.length}
          note={data.critical_blockers.join(", ") || "none"}
        />
        <Stat
          label="Controls"
          value={data.controls.length}
          note={`${unverified.length} not verified`}
        />
      </div>

      {data.critical_blockers.length > 0 ? (
        <Notice tone="danger">
          <strong>Certification is blocked.</strong> The following critical controls
          are not verified: {data.critical_blockers.join(", ")}. A critical failure
          blocks certification regardless of the numerical score.
        </Notice>
      ) : null}

      <Card
        title="Domain scores"
        action={
          <span className="mono muted">
            commit {data.commit_sha.slice(0, 12) || "unknown"} · {formatWhen(data.generated_at)}
          </span>
        }
      >
        <DataTable
          caption="Maturity by domain"
          columns={[
            { key: "domain", label: "Domain" },
            { key: "meter", label: "Score" },
            { key: "score", label: "%", numeric: true },
            { key: "weight", label: "Weight", numeric: true },
            { key: "passed", label: "Verified", numeric: true },
            { key: "failed", label: "Failed", numeric: true },
            { key: "notevidenced", label: "Not evidenced", numeric: true },
          ]}
          rows={Object.entries(data.domain_scores)
            .sort((a, b) => a[1].score - b[1].score)
            .map(([domain, bucket]) => ({
              __key: domain,
              domain: <span className="mono">{domain}</span>,
              meter: (
                <div style={{ minWidth: 140 }}>
                  <Meter value={bucket.score} label={`${domain} maturity`} />
                </div>
              ),
              score: bucket.score.toFixed(1),
              weight: bucket.applicable_weight,
              passed: bucket.passed,
              failed: bucket.failed,
              notevidenced: bucket.not_evidenced,
            }))}
        />
      </Card>

      {unverified.length > 0 ? (
        <Card title={`Not verified (${unverified.length})`}>
          <DataTable
            caption="Controls that are not verified"
            columns={[
              { key: "control", label: "Control" },
              { key: "domain", label: "Domain" },
              { key: "status", label: "Status" },
              { key: "critical", label: "Critical" },
              { key: "weight", label: "Weight", numeric: true },
              { key: "reason", label: "Why" },
            ]}
            rows={unverified.map((control) => ({
              __key: control.control_id,
              control: (
                <>
                  <strong className="mono">{control.control_id}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{control.title}</div>
                </>
              ),
              domain: <span className="mono muted">{control.domain}</span>,
              status: <Status value={control.status} />,
              critical: control.critical ? (
                <span className="badge badge-danger">critical</span>
              ) : (
                <span className="muted">—</span>
              ),
              weight: control.weight,
              reason: <span className="muted">{control.reason ?? "—"}</span>,
            }))}
          />
        </Card>
      ) : null}

      <Card title="All controls">
        <DataTable
          caption="Control catalogue with derived status"
          columns={[
            { key: "control", label: "Control" },
            { key: "domain", label: "Domain" },
            { key: "status", label: "Status" },
            { key: "weight", label: "Weight", numeric: true },
            { key: "test", label: "Evidencing test" },
          ]}
          rows={[...data.controls]
            .sort((a, b) => a.control_id.localeCompare(b.control_id))
            .map((control) => ({
              __key: control.control_id,
              control: (
                <>
                  <strong className="mono">{control.control_id}</strong>
                  {control.critical ? (
                    <>
                      {" "}
                      <span className="badge badge-danger">critical</span>
                    </>
                  ) : null}
                  <div className="muted" style={{ fontSize: 12 }}>{control.title}</div>
                </>
              ),
              domain: <span className="mono muted">{control.domain}</span>,
              status: <Status value={control.status} />,
              weight: control.weight,
              test: (
                <span className="mono muted" style={{ fontSize: 11 }}>
                  {control.test_id || "no automated test"}
                </span>
              ),
            }))}
        />
      </Card>
    </div>
  );
}
