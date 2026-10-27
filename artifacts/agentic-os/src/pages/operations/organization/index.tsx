import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError, formatCost, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Organization {
  tenant: {
    slug: string;
    name: string;
    region: string;
    data_residency: string;
    default_classification: string;
    retention_days: number;
    daily_cost_cap_usd: number;
    status: string;
    org_slug: string;
    org_name: string;
  } | null;
  users: {
    email: string;
    display_name: string;
    clearance: string;
    status: string;
    mfa_enrolled: boolean;
    last_login_at: string | null;
    roles: string[] | null;
  }[];
}

export default function OrganizationPage() {
  const { data, error, status , loading } = useApi<Organization>("/api/v1/organization");
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Organization</h1>
        <SurfaceError error={error ?? ""} status={status} what="organization settings" />
      </>
    );
  }

  return (
    <div className="stack">
      <div>
        <h1>Organization</h1>
        <p className="page-lede">
          Tenant configuration and the people who hold authority within it.
        </p>
      </div>

      {data.tenant ? (
        <Card title={`${data.tenant.org_name} · ${data.tenant.name}`}>
          <DataTable
            caption="Tenant configuration"
            columns={[
              { key: "setting", label: "Setting" },
              { key: "value", label: "Value" },
            ]}
            rows={[
              { __key: "s", setting: "Tenant slug", value: <span className="mono">{data.tenant.slug}</span> },
              { __key: "r", setting: "Region", value: <span className="mono">{data.tenant.region}</span> },
              {
                __key: "d",
                setting: "Data residency",
                value: <span className="mono">{data.tenant.data_residency}</span>,
              },
              {
                __key: "c",
                setting: "Default classification",
                value: <Status value={data.tenant.default_classification} />,
              },
              { __key: "rt", setting: "Retention", value: `${data.tenant.retention_days} days` },
              {
                __key: "b",
                setting: "Daily cost cap",
                value: formatCost(data.tenant.daily_cost_cap_usd),
              },
            ]}
          />
        </Card>
      ) : null}

      <Card title="Users and roles">
        <DataTable
          caption="Users in this tenant"
          columns={[
            { key: "user", label: "User" },
            { key: "roles", label: "Roles" },
            { key: "clearance", label: "Clearance" },
            { key: "mfa", label: "MFA" },
            { key: "status", label: "Status" },
            { key: "last", label: "Last sign-in" },
          ]}
          rows={data.users.map((user) => ({
            __key: user.email,
            user: (
              <>
                <strong>{user.display_name}</strong>
                <div className="mono muted">{user.email}</div>
              </>
            ),
            roles: (
              <span className="mono muted">{(user.roles ?? []).join(", ") || "none"}</span>
            ),
            clearance: <Status value={user.clearance} />,
            mfa: user.mfa_enrolled ? (
              <span className="badge badge-ok">enrolled</span>
            ) : (
              <span className="badge badge-muted">not enrolled</span>
            ),
            status: <Status value={user.status} />,
            last: <span className="mono">{formatWhen(user.last_login_at)}</span>,
          }))}
        />
      </Card>
    </div>
  );
}
