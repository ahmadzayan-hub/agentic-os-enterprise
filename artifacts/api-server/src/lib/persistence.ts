import { createHash, randomBytes, randomUUID, scryptSync, timingSafeEqual } from "node:crypto";
import { pool, type PoolClient } from "@workspace/db";

export const TENANT_ID = "tenant_northstar";
const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? (process.env.NODE_ENV === "production" ? "" : "Northstar!2026");
const DEMO_MFA_CODE = process.env.DEMO_MFA_CODE ?? (process.env.NODE_ENV === "production" ? "" : "123456");

export type Principal = {
  user_id: string;
  email: string;
  display_name: string;
  tenant_id: string;
  organization_id: string;
  roles: string[];
  permissions: string[];
  clearance: string;
  mfa_satisfied: boolean;
};

const hashPassword = (password: string, salt: string) =>
  scryptSync(password, salt, 64).toString("hex");
const hashToken = (token: string) => createHash("sha256").update(token).digest("hex");

export async function ensureSeedIdentity() {
  if (!DEMO_PASSWORD) return;
  const salt = randomBytes(16).toString("hex");
  await pool.query(
    `insert into agentic_tenants (id, name, slug) values ($1,$2,$3)
     on conflict (id) do nothing`,
    [TENANT_ID, "Northstar Enterprise", "northstar-demo"],
  );
  await pool.query(
    `insert into agentic_users
      (id, tenant_id, email, display_name, password_salt, password_hash, roles, permissions, clearance)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9)
     on conflict (tenant_id, email) do update
       set roles=excluded.roles, permissions=excluded.permissions, clearance=excluded.clearance`,
    [
      "usr_01",
      TENANT_ID,
      "alex.morgan@northstar.example",
      "Alex Morgan",
      salt,
      hashPassword(DEMO_PASSWORD, salt),
      ["Platform Administrator", "Approver"],
      [
        "runs:read", "runs:create", "approvals:read", "approvals:decide",
        "agents:read", "knowledge:read", "documents:read", "documents:write",
        "governance:read", "security:read", "security:manage", "organization:read",
        "platform:read",
      ],
      "CONFIDENTIAL",
    ],
  );
}

export async function login(tenantSlug: string, email: string, password: string, mfaCode?: string) {
  await ensureSeedIdentity();
  const result = await pool.query(
    `select u.* from agentic_users u join agentic_tenants t on t.id=u.tenant_id
     where t.slug=$1 and lower(u.email)=lower($2) and u.active=true limit 1`,
    [tenantSlug, email],
  );
  const user = result.rows[0];
  if (!user) return null;
  const actual = Buffer.from(hashPassword(password, user.password_salt), "hex");
  const expected = Buffer.from(user.password_hash, "hex");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) return null;
  const token = randomBytes(32).toString("base64url");
  const expiresAt = new Date(Date.now() + 8 * 60 * 60 * 1000);
  const mfaSatisfied = Boolean(DEMO_MFA_CODE && mfaCode === DEMO_MFA_CODE);
  await pool.query(
    `insert into agentic_sessions (token_hash,user_id,expires_at,mfa_satisfied) values ($1,$2,$3,$4)`,
    [hashToken(token), user.id, expiresAt, mfaSatisfied],
  );
  await pool.query(`update agentic_users set last_login_at=now() where id=$1`, [user.id]);
  return { token, expiresAt, principal: toPrincipal(user, mfaSatisfied) };
}

export async function authenticate(token: string): Promise<Principal | null> {
  const result = await pool.query(
     `select u.*, s.mfa_satisfied from agentic_sessions s join agentic_users u on u.id=s.user_id
     where s.token_hash=$1 and s.revoked_at is null and s.expires_at>now() and u.active=true`,
    [hashToken(token)],
  );
  return result.rows[0] ? toPrincipal(result.rows[0], Boolean(result.rows[0].mfa_satisfied)) : null;
}

export async function logout(token: string) {
  await pool.query(`update agentic_sessions set revoked_at=now() where token_hash=$1`, [hashToken(token)]);
}

function toPrincipal(user: Record<string, unknown>, mfaSatisfied: boolean): Principal {
  return {
    user_id: String(user.id),
    email: String(user.email),
    display_name: String(user.display_name),
    tenant_id: String(user.tenant_id),
    organization_id: "org_northstar",
    roles: user.roles as string[],
    permissions: user.permissions as string[],
    clearance: String(user.clearance),
    mfa_satisfied: mfaSatisfied,
  };
}

export async function listRecords<T>(tenantId: string, type: string): Promise<T[]> {
  const result = await pool.query(
    `select data from agentic_records where tenant_id=$1 and resource_type=$2 order by created_at desc`,
    [tenantId, type],
  );
  return result.rows.map((row) => row.data as T);
}

export async function putRecord(tenantId: string, type: string, id: string, data: unknown) {
  await pool.query(
    `insert into agentic_records (tenant_id,resource_type,resource_id,data)
     values ($1,$2,$3,$4::jsonb)
     on conflict (tenant_id,resource_type,resource_id)
     do update set data=excluded.data, updated_at=now()`,
    [tenantId, type, id, JSON.stringify(data)],
  );
}

export async function seedRecord(type: string, id: string, data: unknown) {
  await pool.query(
    `insert into agentic_records (tenant_id,resource_type,resource_id,data)
     values ($1,$2,$3,$4::jsonb) on conflict do nothing`,
    [TENANT_ID, type, id, JSON.stringify(data)],
  );
}

export async function audit(
  principal: Principal,
  category: string,
  action: string,
  outcome: string,
  resourceType: string,
  resourceId: string,
  detail: unknown = {},
) {
  const client = await pool.connect();
  try {
    await client.query("begin");
    await appendAudit(client, principal, category, action, outcome, resourceType, resourceId, detail);
    await client.query("commit");
  } catch (error) {
    await client.query("rollback");
    throw error;
  } finally {
    client.release();
  }
}

export async function mutateWithAudit(
  principal: Principal,
  records: { type: string; id: string; data: unknown }[],
  event: { category: string; action: string; outcome: string; resourceType: string; resourceId: string; detail?: unknown },
) {
  const client = await pool.connect();
  try {
    await client.query("begin");
    for (const record of records) {
      await client.query(
        `insert into agentic_records (tenant_id,resource_type,resource_id,data)
         values ($1,$2,$3,$4::jsonb)
         on conflict (tenant_id,resource_type,resource_id)
         do update set data=excluded.data, updated_at=now()`,
        [principal.tenant_id, record.type, record.id, stableStringify(record.data)],
      );
    }
    await appendAudit(
      client, principal, event.category, event.action, event.outcome,
      event.resourceType, event.resourceId, event.detail ?? {},
    );
    await client.query("commit");
  } catch (error) {
    await client.query("rollback");
    throw error;
  } finally {
    client.release();
  }
}

async function appendAudit(
  client: PoolClient,
  principal: Principal,
  category: string,
  action: string,
  outcome: string,
  resourceType: string,
  resourceId: string,
  detail: unknown,
) {
  await client.query("select pg_advisory_xact_lock(hashtext($1))", [`audit:${principal.tenant_id}`]);
  const previous = await client.query(
    `select sequence_no, entry_hash from agentic_audit_events
     where tenant_id=$1 order by sequence_no::bigint desc limit 1`,
    [principal.tenant_id],
  );
  const sequenceNo = String(previous.rows[0] ? BigInt(previous.rows[0].sequence_no) + 1n : 1n);
  const previousHash = previous.rows[0]?.entry_hash ?? "GENESIS";
  const occurredAt = new Date().toISOString();
  const payload = stableStringify({
    sequence_no: sequenceNo, tenant_id: principal.tenant_id, actor_user_id: principal.user_id,
    category, action, outcome, resource_type: resourceType, resource_id: resourceId,
    detail, previous_hash: previousHash, occurred_at: occurredAt,
  });
  const entryHash = `sha256:${createHash("sha256").update(payload).digest("hex")}`;
  await client.query(
    `insert into agentic_audit_events
      (id,sequence_no,tenant_id,actor_user_id,category,action,outcome,resource_type,resource_id,detail,previous_hash,entry_hash,occurred_at)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13)`,
    [randomUUID(), sequenceNo, principal.tenant_id, principal.user_id, category, action, outcome, resourceType, resourceId, stableStringify(detail), previousHash, entryHash, occurredAt],
  );
}

export async function listAudit(tenantId: string) {
  const result = await pool.query(
    `select sequence_no::bigint as sequence_no, category, action, outcome, resource_type,
            resource_id, agent_id, tool_id, entry_hash, previous_hash, occurred_at
     from agentic_audit_events where tenant_id=$1 order by sequence_no::bigint desc limit 250`,
    [tenantId],
  );
  return result.rows;
}

export async function verifyAudit(tenantId: string) {
  const result = await pool.query(
    `select sequence_no, tenant_id, actor_user_id, category, action, outcome, resource_type,
            resource_id, detail, previous_hash, entry_hash, occurred_at
     from agentic_audit_events where tenant_id=$1 order by sequence_no::bigint`,
    [tenantId],
  );
  let previousHash = "GENESIS";
  for (const row of result.rows) {
    const payload = stableStringify({
      sequence_no: String(row.sequence_no), tenant_id: row.tenant_id, actor_user_id: row.actor_user_id,
      category: row.category, action: row.action, outcome: row.outcome,
      resource_type: row.resource_type, resource_id: row.resource_id,
      detail: row.detail, previous_hash: row.previous_hash,
      occurred_at: new Date(row.occurred_at).toISOString(),
    });
    const expected = `sha256:${createHash("sha256").update(payload).digest("hex")}`;
    if (row.previous_hash !== previousHash || row.entry_hash !== expected) {
      return { entries_checked: result.rows.length, intact: false, broken_at: Number(row.sequence_no) };
    }
    previousHash = row.entry_hash;
  }
  return { entries_checked: result.rows.length, intact: true, broken_at: null };
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export const demoCredentials = {
  email: "alex.morgan@northstar.example",
  password: DEMO_PASSWORD,
};