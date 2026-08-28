import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Skill {
  skill_key: string;
  name: string;
  description: string;
  owner_team: string;
  execution_mode: string;
  risk_class: string;
  status: string;
  required_tools: string[];
  evaluation_threshold: number;
}

export default function SkillsPage() {
  const { data, error, status , loading } = useApi<{ skills: Skill[] }>("/api/v1/skills");
  if (loading) return <div className="empty">Loading...</div>;

  const deterministic = data?.skills.filter((s) => s.execution_mode === "DETERMINISTIC").length ?? 0;

  return (
    <div className="stack">
      <div>
        <h1>Skills</h1>
        <p className="page-lede">
          Reusable capabilities that execute inside an agent&rsquo;s authority, never
          beside it. {deterministic} of {data?.skills.length ?? 0} run entirely in code:
          the same input produces the same output, with no model involved.
        </p>
      </div>
      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="skills" />
      ) : (
        <Card>
          <DataTable
            caption="Skill registry"
            columns={[
              { key: "skill", label: "Skill" },
              { key: "mode", label: "Execution" },
              { key: "risk", label: "Risk" },
              { key: "tools", label: "Required tools" },
              { key: "threshold", label: "Eval threshold", numeric: true },
              { key: "owner", label: "Owner" },
            ]}
            rows={data.skills.map((skill) => ({
              __key: skill.skill_key,
              skill: (
                <>
                  <strong>{skill.name}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{skill.description}</div>
                </>
              ),
              mode: (
                <Status
                  value={skill.execution_mode}
                  tone={skill.execution_mode === "DETERMINISTIC" ? "ok" : "info"}
                />
              ),
              risk: <Status value={skill.risk_class} />,
              tools: (
                <span className="mono muted">
                  {skill.required_tools?.length ? skill.required_tools.join(", ") : "none"}
                </span>
              ),
              threshold: skill.evaluation_threshold,
              owner: skill.owner_team,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
